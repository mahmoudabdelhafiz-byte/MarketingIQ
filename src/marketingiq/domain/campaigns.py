from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from marketingiq.domain.models import (
    Base,
    DataClassification,
    RedistributionStatus,
    new_id,
    utcnow,
)


class CampaignChannel(enum.StrEnum):
    EMAIL = "EMAIL"


class CampaignDraftStatus(enum.StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class CampaignReviewAction(enum.StrEnum):
    EDIT = "EDIT"
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class CampaignDraft(Base):
    """Append-only, tenant-private outreach copy prepared for human review."""

    __tablename__ = "campaign_drafts"
    __table_args__ = (
        Index(
            "ix_campaign_draft_tenant_relationship_time",
            "organization_id",
            "organization_company_id",
            "created_at",
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
    channel: Mapped[CampaignChannel] = mapped_column(
        Enum(
            CampaignChannel,
            name="campaign_drafts_campaign_channel",
            native_enum=False,
            validate_strings=True,
            create_constraint=True,
        ),
        default=CampaignChannel.EMAIL,
    )
    status: Mapped[CampaignDraftStatus] = mapped_column(
        Enum(
            CampaignDraftStatus,
            name="campaign_drafts_status",
            native_enum=False,
            validate_strings=True,
            create_constraint=True,
        ),
        default=CampaignDraftStatus.DRAFT,
    )
    subject: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    call_to_action: Mapped[str] = mapped_column(String(500))
    message_angle: Mapped[str] = mapped_column(String(100))
    personalization_snapshot: Mapped[Any] = mapped_column(JSON)
    evidence_snapshot: Mapped[Any] = mapped_column(JSON)
    workflow_version: Mapped[str] = mapped_column(String(100))
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    classification: Mapped[DataClassification] = mapped_column(
        Enum(
            DataClassification,
            name="campaign_drafts_data_classification",
            native_enum=False,
            validate_strings=True,
            create_constraint=True,
        ),
        default=DataClassification.MARKETINGIQ_DERIVED,
    )
    redistribution_status: Mapped[RedistributionStatus] = mapped_column(
        Enum(
            RedistributionStatus,
            name="campaign_drafts_redistribution_status",
            native_enum=False,
            validate_strings=True,
            create_constraint=True,
        ),
        default=RedistributionStatus.INTERNAL_ONLY,
    )


class CampaignDraftReviewEvent(Base):
    """Immutable human edit/review event that snapshots the exact reviewed content."""

    __tablename__ = "campaign_draft_review_events"
    __table_args__ = (
        UniqueConstraint("draft_id", "revision_number", name="uq_campaign_review_revision"),
        Index(
            "ix_campaign_review_tenant_draft_time",
            "organization_id",
            "draft_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    draft_id: Mapped[str] = mapped_column(ForeignKey("campaign_drafts.id"), index=True)
    revision_number: Mapped[int] = mapped_column(Integer)
    action: Mapped[CampaignReviewAction] = mapped_column(
        Enum(
            CampaignReviewAction,
            name="campaign_review_action",
            native_enum=False,
            validate_strings=True,
            create_constraint=True,
        )
    )
    status: Mapped[CampaignDraftStatus] = mapped_column(
        Enum(
            CampaignDraftStatus,
            name="campaign_review_status",
            native_enum=False,
            validate_strings=True,
            create_constraint=True,
        )
    )
    subject: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    call_to_action: Mapped[str] = mapped_column(String(500))
    reason: Mapped[str | None] = mapped_column(String(500))
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
