"""Company discovery: provider port, staged candidates, and run history.

Discovery proposes *companies* (never people) that may match a product's active ICP. Candidates
are tenant-private suggestions that stay outside the company catalog until a human accepts them.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from marketingiq.domain.models import Base, DataClassification, new_id, utcnow


class DiscoveryRunStatus(enum.StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class DiscoveryCandidateStatus(enum.StrEnum):
    PROPOSED = "PROPOSED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    SKIPPED = "SKIPPED"


class DiscoverySkipReason(enum.StrEnum):
    INVALID_DOMAIN = "INVALID_DOMAIN"
    UNGROUNDED = "UNGROUNDED"
    ALREADY_ATTACHED = "ALREADY_ATTACHED"
    PREVIOUSLY_REJECTED = "PREVIOUSLY_REJECTED"
    ALREADY_PROPOSED = "ALREADY_PROPOSED"
    DUPLICATE_IN_RUN = "DUPLICATE_IN_RUN"


class DiscoveryProviderError(RuntimeError):
    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


@dataclass(frozen=True)
class DiscoveryCriterion:
    kind: str
    value: str
    required: bool = False


@dataclass(frozen=True)
class DiscoveryRequest:
    product_name: str
    product_description: str | None
    value_proposition: str | None
    criteria: tuple[DiscoveryCriterion, ...]
    employee_min: int | None
    employee_max: int | None
    exclude_domains: tuple[str, ...]
    max_results: int


@dataclass(frozen=True)
class DiscoveredCompany:
    name: str
    domain: str
    source_urls: tuple[str, ...]
    country_code: str | None = None
    industry: str | None = None
    rationale: str | None = None


@dataclass(frozen=True)
class DiscoveryProviderResult:
    provider_key: str
    model: str | None
    companies: tuple[DiscoveredCompany, ...]
    # URLs the provider actually retrieved during this request. A candidate is grounded only
    # when at least one of its source URLs appears here, so a model cannot cite pages it never saw.
    retrieved_urls: frozenset[str] = frozenset()
    searches_used: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class CompanyDiscoveryProvider(Protocol):
    key: str
    model: str | None

    @property
    def configured(self) -> bool: ...

    def discover(self, request: DiscoveryRequest) -> DiscoveryProviderResult: ...


class CompanyDiscoveryRun(Base):
    __tablename__ = "company_discovery_runs"
    __table_args__ = (
        CheckConstraint(
            "requested_max_results >= 1 AND requested_max_results <= 25",
            name="ck_discovery_run_max_results",
        ),
        Index("ix_discovery_run_org_started", "organization_id", "started_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
    icp_id: Mapped[str] = mapped_column(ForeignKey("icps.id"))
    icp_version: Mapped[int] = mapped_column(Integer)
    provider_key: Mapped[str] = mapped_column(String(100))
    model: Mapped[str | None] = mapped_column(String(100))
    workflow_version: Mapped[str] = mapped_column(String(100))
    status: Mapped[DiscoveryRunStatus] = mapped_column(
        Enum(
            DiscoveryRunStatus,
            name="company_discovery_runs_status",
            native_enum=False,
            validate_strings=True,
            create_constraint=True,
        )
    )
    requested_max_results: Mapped[int] = mapped_column(Integer)
    excluded_domain_count: Mapped[int] = mapped_column(Integer, default=0)
    returned_count: Mapped[int] = mapped_column(Integer, default=0)
    proposed_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    searches_used: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    error_category: Mapped[str | None] = mapped_column(String(100))
    initiated_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    candidates: Mapped[list[CompanyDiscoveryCandidate]] = relationship(
        back_populates="run", order_by="CompanyDiscoveryCandidate.position"
    )


class CompanyDiscoveryCandidate(Base):
    __tablename__ = "company_discovery_candidates"
    __table_args__ = (
        Index("ix_discovery_candidate_org_domain", "organization_id", "normalized_domain"),
        Index("ix_discovery_candidate_org_status", "organization_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("company_discovery_runs.id"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    company_name: Mapped[str] = mapped_column(String(255))
    raw_domain: Mapped[str] = mapped_column(String(255))
    normalized_domain: Mapped[str | None] = mapped_column(String(255))
    country_code: Mapped[str | None] = mapped_column(String(2))
    industry: Mapped[str | None] = mapped_column(String(200))
    rationale: Mapped[str | None] = mapped_column(Text)
    source_urls: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[DiscoveryCandidateStatus] = mapped_column(
        Enum(
            DiscoveryCandidateStatus,
            name="company_discovery_candidates_status",
            native_enum=False,
            validate_strings=True,
            create_constraint=True,
        )
    )
    skip_reason: Mapped[DiscoverySkipReason | None] = mapped_column(
        Enum(
            DiscoverySkipReason,
            name="company_discovery_candidates_skip_reason",
            native_enum=False,
            validate_strings=True,
            create_constraint=True,
        )
    )
    classification: Mapped[DataClassification] = mapped_column(
        Enum(
            DataClassification,
            name="company_discovery_candidates_classification",
            native_enum=False,
            validate_strings=True,
            create_constraint=True,
        ),
        default=DataClassification.MARKETINGIQ_DERIVED,
    )
    organization_company_id: Mapped[str | None] = mapped_column(
        ForeignKey("organization_companies.id")
    )
    reviewed_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    run: Mapped[CompanyDiscoveryRun] = relationship(back_populates="candidates")
