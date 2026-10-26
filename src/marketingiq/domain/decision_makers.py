"""Decision-maker discovery: target personas, evidence and the vocabulary used to judge people.

Personas describe *who* a product should reach (titles, departments, seniority). They belong to a
product so they survive ICP revisions. Nothing in this module or the discovery engine hard-codes a
particular product's roles; suggested templates live in the web interface only.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from marketingiq.domain.models import Base, TimestampMixin, new_id, utcnow


class RoleMatch(enum.StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    NONE = "NONE"


class EmploymentStatus(enum.StrEnum):
    CURRENT_CONFIRMED = "CURRENT_CONFIRMED"
    CURRENT_LIKELY = "CURRENT_LIKELY"
    CURRENT_UNCERTAIN = "CURRENT_UNCERTAIN"
    FORMER = "FORMER"
    CONFLICTING = "CONFLICTING"


class Suitability(enum.StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class ContactStatus(enum.StrEnum):
    READY_FOR_DRAFT = "READY_FOR_DRAFT"
    EMAIL_UNVERIFIED = "EMAIL_UNVERIFIED"
    CONTACT_FOUND_EMAIL_MISSING = "CONTACT_FOUND_EMAIL_MISSING"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    NOT_RELEVANT = "NOT_RELEVANT"
    EXCLUDED = "EXCLUDED"


class DiscoveryOutcome(enum.StrEnum):
    READY_FOR_DRAFT = "READY_FOR_DRAFT"
    EMAIL_UNVERIFIED = "EMAIL_UNVERIFIED"
    CONTACT_FOUND_EMAIL_MISSING = "CONTACT_FOUND_EMAIL_MISSING"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    NO_RELEVANT_DECISION_MAKER = "NO_RELEVANT_DECISION_MAKER"
    NO_CONTACTS_FOUND = "NO_CONTACTS_FOUND"


class ReviewStatus(enum.StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    EXCLUDED = "EXCLUDED"


class EvidenceSourceType(enum.StrEnum):
    PROVIDER_RECORD = "PROVIDER_RECORD"
    PROVIDER_SOURCE_PAGE = "PROVIDER_SOURCE_PAGE"
    COMPANY_WEBSITE = "COMPANY_WEBSITE"
    PUBLIC_PROFILE_URL = "PUBLIC_PROFILE_URL"
    EMAIL_VERIFICATION = "EMAIL_VERIFICATION"
    MANUAL = "MANUAL"


class EmailOrigin(enum.StrEnum):
    PROVIDER_FOUND = "PROVIDER_FOUND"
    PATTERN_PREDICTED = "PATTERN_PREDICTED"
    CUSTOMER_PROVIDED = "CUSTOMER_PROVIDED"


class ProductPersona(Base, TimestampMixin):
    __tablename__ = "product_personas"
    __table_args__ = (
        UniqueConstraint("product_id", "name", name="uq_product_persona_name"),
        Index("ix_product_persona_tenant_product", "organization_id", "product_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))
    titles: Mapped[Any] = mapped_column(JSON, default=list)
    departments: Mapped[Any] = mapped_column(JSON, default=list)
    seniorities: Mapped[Any] = mapped_column(JSON, default=list)
    max_contacts: Mapped[int] = mapped_column(Integer, default=2)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class ContactEvidence(Base):
    """One piece of provenance for a claim about a person (name, title, employer, email)."""

    __tablename__ = "contact_evidence"
    __table_args__ = (Index("ix_contact_evidence_contact", "contact_id", "retrieved_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    contact_id: Mapped[str] = mapped_column(ForeignKey("contact_candidates.id", ondelete="CASCADE"))
    source_type: Mapped[str] = mapped_column(String(40))
    provider_key: Mapped[str | None] = mapped_column(String(100))
    provider_reference: Mapped[str | None] = mapped_column(String(255))
    source_url: Mapped[str | None] = mapped_column(String(2048))
    claimed_name: Mapped[str | None] = mapped_column(String(220))
    claimed_title: Mapped[str | None] = mapped_column(String(255))
    claimed_company: Mapped[str | None] = mapped_column(String(255))
    snippet: Mapped[str | None] = mapped_column(String(500))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_on: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    still_on_page: Mapped[bool | None] = mapped_column(Boolean)
    confidence: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
