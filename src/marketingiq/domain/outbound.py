from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from marketingiq.domain.models import Base, new_id, utcnow


class OutboundSendStatus(enum.StrEnum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"


class SuppressionSource(enum.StrEnum):
    MANUAL = "MANUAL"
    OPT_OUT = "OPT_OUT"
    BOUNCE = "BOUNCE"
    COMPLAINT = "COMPLAINT"


class OutboundProviderError(RuntimeError):
    category = "PROVIDER_ERROR"


class OutboundProviderNotConfigured(OutboundProviderError):
    category = "NOT_CONFIGURED"


class OutboundProviderAuthenticationError(OutboundProviderError):
    category = "AUTHENTICATION_ERROR"


class OutboundProviderRejected(OutboundProviderError):
    category = "REJECTED"


@dataclass(frozen=True)
class OutboundMessage:
    recipient: str
    subject: str
    body: str
    idempotency_key: str


@dataclass(frozen=True)
class OutboundSendResult:
    provider_key: str
    accepted: bool
    provider_message_id: str | None = None


class EmailSender(Protocol):
    key: str

    @property
    def configured(self) -> bool: ...

    def send(self, message: OutboundMessage) -> OutboundSendResult: ...


class OutboundSendAttempt(Base):
    __tablename__ = "outbound_send_attempts"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "idempotency_key",
            name="uq_outbound_send_org_idempotency",
        ),
        Index(
            "ix_outbound_send_tenant_draft_time",
            "organization_id",
            "draft_id",
            "requested_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    organization_company_id: Mapped[str] = mapped_column(
        ForeignKey("organization_companies.id"), index=True
    )
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    draft_id: Mapped[str] = mapped_column(ForeignKey("campaign_drafts.id"), index=True)
    review_event_id: Mapped[str] = mapped_column(
        ForeignKey("campaign_draft_review_events.id"), index=True
    )
    contact_id: Mapped[str] = mapped_column(ForeignKey("contact_candidates.id"), index=True)
    contact_email_id: Mapped[str] = mapped_column(ForeignKey("contact_emails.id"), index=True)
    provider_key: Mapped[str] = mapped_column(String(50))
    idempotency_key: Mapped[str] = mapped_column(String(100))
    status: Mapped[OutboundSendStatus] = mapped_column(
        Enum(
            OutboundSendStatus,
            name="outbound_send_status",
            native_enum=False,
            validate_strings=True,
            create_constraint=True,
        ),
        default=OutboundSendStatus.PENDING,
    )
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    error_category: Mapped[str | None] = mapped_column(String(50))
    requested_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SuppressionEntry(Base):
    __tablename__ = "outbound_suppressions"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "email_normalized",
            name="uq_outbound_suppression_org_email",
        ),
        Index(
            "ix_outbound_suppression_tenant_time",
            "organization_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    email_normalized: Mapped[str] = mapped_column(String(320))
    source: Mapped[SuppressionSource] = mapped_column(
        Enum(
            SuppressionSource,
            name="outbound_suppression_source",
            native_enum=False,
            validate_strings=True,
            create_constraint=True,
        )
    )
    reason: Mapped[str | None] = mapped_column(String(500))
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
