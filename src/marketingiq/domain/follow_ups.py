"""Follow-up emails sent in the same thread when a prospect has not replied."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from marketingiq.domain.models import Base, new_id, utcnow


class FollowUpStatus(enum.StrEnum):
    SCHEDULED = "SCHEDULED"  # waiting for its due time
    AWAITING_APPROVAL = "AWAITING_APPROVAL"  # due; a person must approve (or skip) it
    SENT = "SENT"
    SKIPPED = "SKIPPED"  # stopped automatically (reply, bounce, opt-out...) or by a person
    FAILED = "FAILED"


ACTIVE_FOLLOW_UP_STATUSES = (FollowUpStatus.SCHEDULED, FollowUpStatus.AWAITING_APPROVAL)


class OutboundFollowUp(Base):
    __tablename__ = "outbound_follow_ups"
    __table_args__ = (
        UniqueConstraint("initial_attempt_id", "step", name="uq_follow_up_attempt_step"),
        CheckConstraint("step >= 1 AND step <= 2", name="ck_outbound_follow_up_step"),
        Index("ix_follow_up_status_due", "status", "due_at"),
        Index("ix_follow_up_org_status", "organization_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    organization_company_id: Mapped[str] = mapped_column(
        ForeignKey("organization_companies.id"), index=True
    )
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    initial_attempt_id: Mapped[str] = mapped_column(
        ForeignKey("outbound_send_attempts.id"), index=True
    )
    draft_id: Mapped[str] = mapped_column(ForeignKey("campaign_drafts.id"), index=True)
    contact_id: Mapped[str] = mapped_column(ForeignKey("contact_candidates.id"), index=True)
    contact_email_id: Mapped[str] = mapped_column(ForeignKey("contact_emails.id"))
    step: Mapped[int] = mapped_column(Integer)
    status: Mapped[FollowUpStatus] = mapped_column(
        Enum(
            FollowUpStatus,
            name="outbound_follow_up_status",
            native_enum=False,
            validate_strings=True,
            create_constraint=True,
        ),
        default=FollowUpStatus.SCHEDULED,
    )
    auto_send: Mapped[bool] = mapped_column(Boolean, default=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    subject: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    decided_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    provider_key: Mapped[str | None] = mapped_column(String(50))
    provider_message_id: Mapped[str | None] = mapped_column(String(255), index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stop_reason: Mapped[str | None] = mapped_column(String(50))
