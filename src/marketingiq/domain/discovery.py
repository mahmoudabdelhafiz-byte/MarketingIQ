"""Scheduled company discovery: find new target companies that match a product's ICP."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from marketingiq.domain.models import Base, TimestampMixin, new_id, utcnow

HEADCOUNT_BUCKETS = (
    "1-10",
    "11-50",
    "51-200",
    "201-500",
    "501-1000",
    "1001-5000",
    "5001-10000",
    "10001+",
)


class CompanyDiscoveryRunStatus(enum.StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class DiscoveryCriteria:
    query: str | None = None
    countries: tuple[str, ...] = ()
    industries: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    headcounts: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not (
            self.query or self.countries or self.industries or self.keywords or self.headcounts
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "countries": list(self.countries),
            "industries": list(self.industries),
            "keywords": list(self.keywords),
            "headcounts": list(self.headcounts),
        }


@dataclass(frozen=True)
class DiscoveredCompany:
    domain: str
    name: str
    # Evidence for AI web-search results: pages that were actually retrieved, and why it fits.
    source_urls: tuple[str, ...] = ()
    rationale: str | None = None


@dataclass(frozen=True)
class DiscoveryResult:
    provider_key: str
    companies: tuple[DiscoveredCompany, ...]
    total_results: int | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)
    # Spend accounting for paid sources (searches, tokens, model); never provider payloads.
    usage: dict[str, Any] = field(default_factory=dict)


class CompanyDiscoveryProvider(Protocol):
    key: str

    @property
    def configured(self) -> bool: ...

    def discover(self, criteria: DiscoveryCriteria, offset: int = 0) -> DiscoveryResult: ...


class CompanyDiscoveryPolicy(Base, TimestampMixin):
    __tablename__ = "company_discovery_policies"
    __table_args__ = (
        CheckConstraint(
            "cadence_minutes >= 60 AND cadence_minutes <= 43200",
            name="ck_company_discovery_cadence",
        ),
        CheckConstraint(
            "max_new_per_run >= 1 AND max_new_per_run <= 100",
            name="ck_company_discovery_max_new",
        ),
        Index("ix_company_discovery_due", "organization_id", "enabled", "next_run_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    provider_key: Mapped[str] = mapped_column(String(50), default="HUNTER")
    query: Mapped[str | None] = mapped_column(String(500))
    countries: Mapped[Any] = mapped_column(JSON, default=list)
    industries: Mapped[Any] = mapped_column(JSON, default=list)
    keywords: Mapped[Any] = mapped_column(JSON, default=list)
    headcounts: Mapped[Any] = mapped_column(JSON, default=list)
    max_new_per_run: Mapped[int] = mapped_column(Integer, default=20)
    cadence_minutes: Mapped[int] = mapped_column(Integer, default=1440)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    run_workflow: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_provider_credits: Mapped[bool] = mapped_column(Boolean, default=False)
    next_offset: Mapped[int] = mapped_column(Integer, default=0)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str | None] = mapped_column(String(30))
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    run_as_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))

    def criteria(self) -> DiscoveryCriteria:
        return DiscoveryCriteria(
            query=self.query or None,
            countries=tuple(self.countries or ()),
            industries=tuple(self.industries or ()),
            keywords=tuple(self.keywords or ()),
            headcounts=tuple(self.headcounts or ()),
        )


class CompanyDiscoveryRun(Base):
    __tablename__ = "company_discovery_runs"
    __table_args__ = (
        UniqueConstraint("policy_id", "scheduled_for", name="uq_company_discovery_run_schedule"),
        Index("ix_company_discovery_run_tenant_time", "organization_id", "started_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    policy_id: Mapped[str] = mapped_column(
        ForeignKey("company_discovery_policies.id"), index=True
    )
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[CompanyDiscoveryRunStatus] = mapped_column(
        Enum(
            CompanyDiscoveryRunStatus,
            native_enum=False,
            validate_strings=True,
            create_constraint=True,
            name="company_discovery_runs_status",
        ),
        default=CompanyDiscoveryRunStatus.RUNNING,
    )
    candidates_found: Mapped[int] = mapped_column(Integer, default=0)
    companies_added: Mapped[int] = mapped_column(Integer, default=0)
    already_known: Mapped[int] = mapped_column(Integer, default=0)
    workflows_completed: Mapped[int] = mapped_column(Integer, default=0)
    workflows_failed: Mapped[int] = mapped_column(Integer, default=0)
    added_relationship_ids: Mapped[Any] = mapped_column(JSON, default=list)
    notes: Mapped[Any] = mapped_column(JSON, default=list)
    error_code: Mapped[str | None] = mapped_column(String(100))
    run_as_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    triggered_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True)
