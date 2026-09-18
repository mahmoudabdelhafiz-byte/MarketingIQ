from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from marketingiq.domain.models import Base, new_id, utcnow


class CampaignAIAttemptStatus(enum.StrEnum):
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    FALLBACK = "FALLBACK"


@dataclass(frozen=True)
class GroundingFact:
    fact_id: str
    fact_key: str
    value: str


@dataclass(frozen=True)
class CampaignAIRequest:
    company_name: str
    recipient_first_name: str | None
    buyer_role: str
    product_name: str
    value_proposition: str
    call_to_action: str
    grounding_facts: tuple[GroundingFact, ...]


@dataclass(frozen=True)
class CampaignAIResult:
    subject: str
    body: str
    used_fact_keys: tuple[str, ...]
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class CampaignAIProviderError(RuntimeError):
    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


class CampaignAIProvider(Protocol):
    key: str
    configured: bool
    model: str

    def generate(self, request: CampaignAIRequest) -> CampaignAIResult: ...


class CampaignAIGenerationAttempt(Base):
    """Safe metadata for one AI-assisted generation attempt; never stores prompt or raw output."""

    __tablename__ = "campaign_ai_generation_attempts"
    __table_args__ = (
        Index(
            "ix_campaign_ai_attempt_tenant_time",
            "organization_id",
            "requested_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    organization_company_id: Mapped[str] = mapped_column(
        ForeignKey("organization_companies.id"), index=True
    )
    qualification_id: Mapped[str] = mapped_column(ForeignKey("lead_qualifications.id"), index=True)
    contact_id: Mapped[str] = mapped_column(ForeignKey("contact_candidates.id"), index=True)
    draft_id: Mapped[str | None] = mapped_column(ForeignKey("campaign_drafts.id"), index=True)
    provider_key: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(150))
    prompt_version: Mapped[str] = mapped_column(String(100))
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    input_fact_keys: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(30))
    response_status: Mapped[str] = mapped_column(String(100))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    error_category: Mapped[str | None] = mapped_column(String(100))
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
