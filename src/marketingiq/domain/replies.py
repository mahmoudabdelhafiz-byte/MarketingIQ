"""Prospect replies captured from the shared mailbox, with an assisted answer."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from marketingiq.domain.models import Base, new_id, utcnow


class ReplyIntent(enum.StrEnum):
    MEETING_REQUEST = "MEETING_REQUEST"  # wants to talk / asks for times
    INTERESTED = "INTERESTED"
    QUESTION = "QUESTION"
    NOT_NOW = "NOT_NOW"
    NOT_INTERESTED = "NOT_INTERESTED"
    UNSUBSCRIBE = "UNSUBSCRIBE"
    OUT_OF_OFFICE = "OUT_OF_OFFICE"
    OTHER = "OTHER"


class ReplyStatus(enum.StrEnum):
    NEW = "NEW"
    ANSWERED = "ANSWERED"
    DISMISSED = "DISMISSED"


class InboundReply(Base):
    __tablename__ = "inbound_replies"
    __table_args__ = (
        UniqueConstraint("organization_id", "message_key", name="uq_inbound_reply_message"),
        Index("ix_inbound_reply_org_status", "organization_id", "status", "received_at"),
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
    # Deduplication: the incoming Message-ID, or the mailbox event id when it has none.
    message_key: Mapped[str] = mapped_column(String(150))
    message_id: Mapped[str | None] = mapped_column(String(255))
    from_email: Mapped[str | None] = mapped_column(String(320))
    subject: Mapped[str] = mapped_column(String(255), default="")
    body_text: Mapped[str] = mapped_column(Text, default="")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[ReplyStatus] = mapped_column(
        Enum(
            ReplyStatus,
            name="inbound_reply_status",
            native_enum=False,
            validate_strings=True,
            create_constraint=True,
        ),
        default=ReplyStatus.NEW,
    )
    intent: Mapped[ReplyIntent] = mapped_column(
        Enum(
            ReplyIntent,
            name="inbound_reply_intent",
            native_enum=False,
            validate_strings=True,
            create_constraint=True,
        ),
        default=ReplyIntent.OTHER,
    )
    intent_source: Mapped[str] = mapped_column(String(20), default="RULES")  # RULES | AI | USER
    confidence: Mapped[float | None] = mapped_column(Float)
    summary: Mapped[str | None] = mapped_column(String(500))
    language: Mapped[str] = mapped_column(String(10), default="en")
    suggested_subject: Mapped[str | None] = mapped_column(String(255))
    suggested_body: Mapped[str | None] = mapped_column(Text)
    suggested_slots: Mapped[list] = mapped_column(JSON, default=list)
    suggestion_model: Mapped[str | None] = mapped_column(String(100))
    answer_subject: Mapped[str | None] = mapped_column(String(255))
    answer_body: Mapped[str | None] = mapped_column(Text)
    answer_message_id: Mapped[str | None] = mapped_column(String(255), index=True)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    handled_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    outcome: Mapped[str | None] = mapped_column(String(40))  # MEETING_BOOKED, UNSUBSCRIBED, ...
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
