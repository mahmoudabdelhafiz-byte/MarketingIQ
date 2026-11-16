"""Per-organization unit prices used to turn recorded usage into estimated spend."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from marketingiq.domain.models import Base, new_id, utcnow


class OrganizationCostSettings(Base):
    __tablename__ = "organization_cost_settings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), unique=True, index=True
    )
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    # Per Hunter credit; per million AI tokens; per AI web search; monthly subscriptions.
    provider_credit_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    ai_input_per_million: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    ai_output_per_million: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    web_search_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    monthly_fixed_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    updated_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
