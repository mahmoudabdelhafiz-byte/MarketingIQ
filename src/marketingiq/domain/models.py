from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class DataClassification(enum.StrEnum):
    PUBLIC_EVIDENCE = "PUBLIC_EVIDENCE"
    THIRD_PARTY_LICENSED = "THIRD_PARTY_LICENSED"
    CUSTOMER_PROVIDED = "CUSTOMER_PROVIDED"
    MARKETINGIQ_DERIVED = "MARKETINGIQ_DERIVED"


class RedistributionStatus(enum.StrEnum):
    UNKNOWN = "UNKNOWN"
    ALLOWED = "ALLOWED"
    RESTRICTED = "RESTRICTED"
    INTERNAL_ONLY = "INTERNAL_ONLY"


class ReviewAction(enum.StrEnum):
    APPROVE = "APPROVE"
    SELECT = "SELECT"
    MANUAL_CORRECTION = "MANUAL_CORRECTION"
    RESOLVE_CONFLICT = "RESOLVE_CONFLICT"


class MembershipRole(enum.StrEnum):
    ORGANIZATION_ADMIN = "ORGANIZATION_ADMIN"
    MARKETING_USER = "MARKETING_USER"
    READ_ONLY = "READ_ONLY"


class ProductStatus(enum.StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class ResearchStatus(enum.StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class ResearchMode(enum.StrEnum):
    PUBLIC_ONLY = "PUBLIC_ONLY"
    PUBLIC_THEN_EXTERNAL = "PUBLIC_THEN_EXTERNAL"
    EXTERNAL_ONLY = "EXTERNAL_ONLY"


class FitGrade(enum.StrEnum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    UNKNOWN = "UNKNOWN"


class FitStatus(enum.StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    NEEDS_MORE_RESEARCH = "NEEDS_MORE_RESEARCH"
    CONFLICTED = "CONFLICTED"
    STALE = "STALE"


class Organization(Base, TimestampMixin):
    __tablename__ = "organizations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class User(Base, TimestampMixin):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_super_admin: Mapped[bool] = mapped_column(Boolean, default=False)


class OrganizationMembership(Base, TimestampMixin):
    __tablename__ = "organization_memberships"
    __table_args__ = (UniqueConstraint("organization_id", "user_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    role: Mapped[MembershipRole] = mapped_column(
        Enum(MembershipRole, native_enum=False, validate_strings=True, create_constraint=True)
    )
    organization: Mapped[Organization] = relationship()
    user: Mapped[User] = relationship()


class Product(Base, TimestampMixin):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("organization_id", "slug"),
        UniqueConstraint("id", "organization_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    value_proposition: Mapped[str | None] = mapped_column(Text)
    employee_min: Mapped[int | None] = mapped_column(Integer)
    employee_max: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[ProductStatus] = mapped_column(
        Enum(ProductStatus, native_enum=False, validate_strings=True, create_constraint=True),
        default=ProductStatus.DRAFT,
    )
    organization: Mapped[Organization] = relationship()
    criteria: Mapped[list[ProductCriterion]] = relationship(cascade="all, delete-orphan")
    icps: Mapped[list[ICP]] = relationship(back_populates="product", cascade="all, delete-orphan")


class ProductCriterion(Base):
    """Normalized repeatable targeting values (country, industry, persona, signal, etc.)."""

    __tablename__ = "product_criteria"
    __table_args__ = (UniqueConstraint("product_id", "kind", "value"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(40))
    value: Mapped[str] = mapped_column(Text)


class ICP(Base, TimestampMixin):
    __tablename__ = "icps"
    __table_args__ = (
        UniqueConstraint("product_id", "name", "version"),
        ForeignKeyConstraint(
            ["product_id", "organization_id"],
            ["products.id", "products.organization_id"],
            ondelete="CASCADE",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    product_id: Mapped[str] = mapped_column(String(36))
    name: Mapped[str] = mapped_column(String(200))
    employee_min: Mapped[int | None] = mapped_column(Integer)
    employee_max: Mapped[int | None] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    product: Mapped[Product] = relationship(back_populates="icps")
    criteria: Mapped[list[ICPCriterion]] = relationship(cascade="all, delete-orphan")


class ICPCriterion(Base):
    __tablename__ = "icp_criteria"
    __table_args__ = (UniqueConstraint("icp_id", "kind", "value"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    icp_id: Mapped[str] = mapped_column(ForeignKey("icps.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(40))
    value: Mapped[str] = mapped_column(Text)
    weight: Mapped[int] = mapped_column(Integer, default=3)
    required: Mapped[bool] = mapped_column(Boolean, default=False)


class Company(Base, TimestampMixin):
    """Global identity only; tenant-private fields belong on tenant-owned records."""

    __tablename__ = "companies"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    canonical_name: Mapped[str] = mapped_column(String(255), index=True)
    website_url: Mapped[str | None] = mapped_column(String(2048))
    country_code: Mapped[str | None] = mapped_column(String(2))
    industry: Mapped[str | None] = mapped_column(String(200))
    employee_min: Mapped[int | None] = mapped_column(Integer)
    employee_max: Mapped[int | None] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_researched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    identifiers: Mapped[list[CompanyIdentifier]] = relationship(cascade="all, delete-orphan")


class CompanyIdentifier(Base, TimestampMixin):
    __tablename__ = "company_identifiers"
    __table_args__ = (UniqueConstraint("kind", "normalized_value"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(50))
    value: Mapped[str] = mapped_column(String(500))
    normalized_value: Mapped[str] = mapped_column(String(500))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)


class OrganizationCompany(Base, TimestampMixin):
    """Tenant-private relationship to a global company identity."""

    __tablename__ = "organization_companies"
    __table_args__ = (UniqueConstraint("organization_id", "company_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"))
    lifecycle_status: Mapped[str | None] = mapped_column(String(50))
    private_notes: Mapped[str | None] = mapped_column(Text)
    organization: Mapped[Organization] = relationship()
    company: Mapped[Company] = relationship()


class ProductFitAssessment(Base):
    """Immutable, versioned result built from a safe current-best fact snapshot."""

    __tablename__ = "product_fit_assessments"
    __table_args__ = (
        CheckConstraint("score >= 0 AND score <= 100", name="ck_fit_score"),
        CheckConstraint(
            "evidence_coverage >= 0 AND evidence_coverage <= 100", name="ck_fit_coverage"
        ),
        Index(
            "ix_fit_tenant_relationship_time",
            "organization_id",
            "organization_company_id",
            "evaluated_at",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    organization_company_id: Mapped[str] = mapped_column(
        ForeignKey("organization_companies.id"), index=True
    )
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
    icp_id: Mapped[str] = mapped_column(ForeignKey("icps.id"), index=True)
    icp_version: Mapped[int] = mapped_column(Integer)
    score: Mapped[int] = mapped_column(Integer)
    evidence_coverage: Mapped[int] = mapped_column(Integer)
    confidence: Mapped[str] = mapped_column(String(20))
    grade: Mapped[FitGrade] = mapped_column(
        Enum(FitGrade, native_enum=False, validate_strings=True, create_constraint=True)
    )
    status: Mapped[FitStatus] = mapped_column(
        Enum(FitStatus, native_enum=False, validate_strings=True, create_constraint=True)
    )
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    workflow_version: Mapped[str] = mapped_column(String(100))
    evidence_snapshot: Mapped[Any] = mapped_column(JSON)
    explanation: Mapped[Any] = mapped_column(JSON)
    classification: Mapped[DataClassification] = mapped_column(
        Enum(DataClassification, native_enum=False, validate_strings=True, create_constraint=True),
        default=DataClassification.MARKETINGIQ_DERIVED,
    )
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))


class DataSource(Base, TimestampMixin):
    __tablename__ = "data_sources"
    __table_args__ = (UniqueConstraint("provider_key", "external_reference"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider_key: Mapped[str] = mapped_column(String(100))
    display_name: Mapped[str] = mapped_column(String(200))
    external_reference: Mapped[str | None] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class ResearchRun(Base, TimestampMixin):
    __tablename__ = "research_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str | None] = mapped_column(ForeignKey("organizations.id"), index=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    initiated_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    purpose: Mapped[str] = mapped_column(String(100))
    mode: Mapped[ResearchMode] = mapped_column(
        Enum(ResearchMode, native_enum=False, validate_strings=True, create_constraint=True),
        default=ResearchMode.PUBLIC_ONLY,
    )
    status: Mapped[ResearchStatus] = mapped_column(
        Enum(ResearchStatus, native_enum=False, validate_strings=True, create_constraint=True),
        default=ResearchStatus.PENDING,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    providers_attempted: Mapped[list[str]] = mapped_column(JSON, default=list)
    providers_succeeded: Mapped[list[str]] = mapped_column(JSON, default=list)
    error_summary: Mapped[str | None] = mapped_column(Text)
    workflow_version: Mapped[str | None] = mapped_column(String(100))


class ProviderUsage(Base):
    """Sanitized accounting record; provider response bodies never belong here."""

    __tablename__ = "provider_usage"
    __table_args__ = (Index("ix_provider_usage_org_requested", "organization_id", "requested_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider_key: Mapped[str] = mapped_column(String(100), index=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    operation: Mapped[str] = mapped_column(String(100))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    response_status: Mapped[str] = mapped_column(String(50), default="PENDING")
    credits_used: Mapped[int | None] = mapped_column(Integer)
    estimated_cost: Mapped[Any | None] = mapped_column(Numeric(12, 4))
    credits_remaining: Mapped[int | None] = mapped_column(Integer)
    request_identifier: Mapped[str | None] = mapped_column(String(255))
    error_category: Mapped[str | None] = mapped_column(String(100))
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)


class CompanyFact(Base):
    """Immutable claim revision; resolution of the best-known value is an application concern."""

    __tablename__ = "company_facts"
    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 100", name="ck_fact_confidence"),
        Index("ix_fact_company_key_observed", "company_id", "fact_key", "observed_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    organization_id: Mapped[str | None] = mapped_column(ForeignKey("organizations.id"), index=True)
    fact_key: Mapped[str] = mapped_column(String(150))
    value: Mapped[Any] = mapped_column(JSON)
    classification: Mapped[DataClassification] = mapped_column(
        Enum(DataClassification, native_enum=False, validate_strings=True, create_constraint=True)
    )
    redistribution_status: Mapped[RedistributionStatus] = mapped_column(
        Enum(
            RedistributionStatus,
            native_enum=False,
            validate_strings=True,
            create_constraint=True,
        ),
        default=RedistributionStatus.UNKNOWN,
    )
    confidence: Mapped[int] = mapped_column(Integer)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    model_version: Mapped[str | None] = mapped_column(String(100))
    research_run_id: Mapped[str | None] = mapped_column(ForeignKey("research_runs.id"))
    evidences: Mapped[list[Evidence]] = relationship(cascade="all, delete-orphan")
    overrides: Mapped[list[HumanOverride]] = relationship(cascade="all, delete-orphan")


class Evidence(Base):
    __tablename__ = "evidence"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_fact_id: Mapped[str] = mapped_column(ForeignKey("company_facts.id", ondelete="CASCADE"))
    data_source_id: Mapped[str | None] = mapped_column(ForeignKey("data_sources.id"))
    reference_url: Mapped[str | None] = mapped_column(String(2048))
    reference_text: Mapped[str | None] = mapped_column(Text)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[DataSource | None] = relationship()


class HumanOverride(Base):
    __tablename__ = "human_overrides"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    company_fact_id: Mapped[str] = mapped_column(ForeignKey("company_facts.id"))
    fact_key: Mapped[str] = mapped_column(String(150), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    action: Mapped[ReviewAction] = mapped_column(
        Enum(ReviewAction, native_enum=False, validate_strings=True, create_constraint=True)
    )
    replacement_value: Mapped[Any] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_tenant_time", "organization_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str | None] = mapped_column(ForeignKey("organizations.id"))
    actor_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(100))
    entity_type: Mapped[str] = mapped_column(String(100))
    entity_id: Mapped[str] = mapped_column(String(36))
    metadata_json: Mapped[Any | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
