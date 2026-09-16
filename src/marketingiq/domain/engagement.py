from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from marketingiq.domain.models import Base, new_id, utcnow


class EngagementEventType(enum.StrEnum):
    DELIVERED = "DELIVERED"
    BOUNCED = "BOUNCED"
    COMPLAINT = "COMPLAINT"
    REPLIED = "REPLIED"
    POSITIVE_REPLY = "POSITIVE_REPLY"
    NEGATIVE_REPLY = "NEGATIVE_REPLY"
    OPT_OUT = "OPT_OUT"


class EngagementSource(enum.StrEnum):
    MANUAL = "MANUAL"
    PROVIDER = "PROVIDER"
    REPLY_INGESTION = "REPLY_INGESTION"
    SYSTEM = "SYSTEM"


class OutreachEngagementEvent(Base):
    __tablename__ = "outreach_engagement_events"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "event_key",
            name="uq_outreach_engagement_org_event_key",
        ),
        UniqueConstraint(
            "organization_id",
            "provider_key",
            "provider_event_id",
            name="uq_outreach_engagement_provider_event",
        ),
        Index(
            "ix_outreach_engagement_tenant_attempt_time",
            "organization_id",
            "send_attempt_id",
            "occurred_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    organization_company_id: Mapped[str] = mapped_column(
        ForeignKey("organization_companies.id"), index=True
    )
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    send_attempt_id: Mapped[str] = mapped_column(
        ForeignKey("outbound_send_attempts.id"), index=True
    )
    draft_id: Mapped[str] = mapped_column(ForeignKey("campaign_drafts.id"), index=True)
    contact_id: Mapped[str] = mapped_column(ForeignKey("contact_candidates.id"), index=True)
    contact_email_id: Mapped[str] = mapped_column(ForeignKey("contact_emails.id"), index=True)
    event_key: Mapped[str] = mapped_column(String(100))
    event_type: Mapped[EngagementEventType] = mapped_column(
        Enum(
            EngagementEventType,
            name="outreach_engagement_event_type",
            native_enum=False,
            validate_strings=True,
            create_constraint=True,
        )
    )
    source: Mapped[EngagementSource] = mapped_column(
        Enum(
            EngagementSource,
            name="outreach_engagement_source",
            native_enum=False,
            validate_strings=True,
            create_constraint=True,
        )
    )
    provider_key: Mapped[str | None] = mapped_column(String(50), index=True)
    provider_event_id: Mapped[str | None] = mapped_column(String(150), index=True)
    reason_code: Mapped[str | None] = mapped_column(String(100))
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
