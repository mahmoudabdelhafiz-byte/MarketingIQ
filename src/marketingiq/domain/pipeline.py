from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from marketingiq.domain.models import Base, new_id, utcnow


class OpportunityStage(enum.StrEnum):
    CONTACTED = "CONTACTED"
    RESPONDED = "RESPONDED"
    MEETING = "MEETING"
    PROPOSAL = "PROPOSAL"
    WON = "WON"
    LOST = "LOST"


class SalesOpportunity(Base):
    __tablename__ = "sales_opportunities"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "send_attempt_id",
            name="uq_sales_opportunity_org_send_attempt",
        ),
        Index(
            "ix_sales_opportunity_tenant_stage_time",
            "organization_id",
            "stage",
            "updated_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    organization_company_id: Mapped[str] = mapped_column(
        ForeignKey("organization_companies.id"), index=True
    )
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
    qualification_id: Mapped[str] = mapped_column(
        ForeignKey("lead_qualifications.id"), index=True
    )
    contact_id: Mapped[str] = mapped_column(ForeignKey("contact_candidates.id"), index=True)
    draft_id: Mapped[str] = mapped_column(ForeignKey("campaign_drafts.id"), index=True)
    send_attempt_id: Mapped[str] = mapped_column(
        ForeignKey("outbound_send_attempts.id"), index=True
    )
    stage: Mapped[OpportunityStage] = mapped_column(
        Enum(
            OpportunityStage,
            name="sales_opportunity_stage",
            native_enum=False,
            validate_strings=True,
            create_constraint=True,
        ),
        default=OpportunityStage.CONTACTED,
    )
    estimated_value: Mapped[float | None] = mapped_column(Numeric(14, 2))
    currency: Mapped[str | None] = mapped_column(String(3))
    next_action: Mapped[str | None] = mapped_column(String(500))
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SalesOpportunityStageEvent(Base):
    __tablename__ = "sales_opportunity_stage_events"
    __table_args__ = (
        UniqueConstraint(
            "opportunity_id",
            "sequence_number",
            name="uq_sales_opportunity_stage_sequence",
        ),
        Index(
            "ix_sales_opportunity_stage_event_tenant_time",
            "organization_id",
            "occurred_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    opportunity_id: Mapped[str] = mapped_column(ForeignKey("sales_opportunities.id"), index=True)
    sequence_number: Mapped[int] = mapped_column()
    from_stage: Mapped[OpportunityStage | None] = mapped_column(
        Enum(
            OpportunityStage,
            name="sales_opportunity_event_from_stage",
            native_enum=False,
            validate_strings=True,
            create_constraint=True,
        )
    )
    to_stage: Mapped[OpportunityStage] = mapped_column(
        Enum(
            OpportunityStage,
            name="sales_opportunity_event_to_stage",
            native_enum=False,
            validate_strings=True,
            create_constraint=True,
        )
    )
    note: Mapped[str | None] = mapped_column(Text)
    reason_code: Mapped[str | None] = mapped_column(String(100))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
