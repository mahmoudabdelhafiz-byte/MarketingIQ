from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from marketingiq.domain.models import Base, TimestampMixin, new_id, utcnow


class AutomationPolicyRunStatus(enum.StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class AutomationPolicy(Base, TimestampMixin):
    __tablename__ = "automation_policies"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "organization_company_id",
            "product_id",
            name="uq_automation_policy_scope",
        ),
        CheckConstraint(
            "cadence_minutes >= 60 AND cadence_minutes <= 43200",
            name="ck_automation_policy_cadence",
        ),
        CheckConstraint(
            "max_contacts >= 1 AND max_contacts <= 50",
            name="ck_automation_policy_max_contacts",
        ),
        CheckConstraint(
            "max_steps >= 1 AND max_steps <= 4",
            name="ck_automation_policy_max_steps",
        ),
        Index(
            "ix_automation_policy_due",
            "organization_id",
            "enabled",
            "next_run_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    organization_company_id: Mapped[str] = mapped_column(
        ForeignKey("organization_companies.id"), index=True
    )
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    cadence_minutes: Mapped[int] = mapped_column(Integer)
    allow_provider_credits: Mapped[bool] = mapped_column(Boolean, default=False)
    contact_provider: Mapped[str] = mapped_column(String(100), default="HUNTER")
    max_contacts: Mapped[int] = mapped_column(Integer, default=10)
    max_steps: Mapped[int] = mapped_column(Integer, default=4)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str | None] = mapped_column(String(30))
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    run_as_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))


class AutomationPolicyRun(Base):
    __tablename__ = "automation_policy_runs"
    __table_args__ = (
        UniqueConstraint(
            "policy_id",
            "scheduled_for",
            name="uq_automation_policy_run_schedule",
        ),
        Index(
            "ix_automation_policy_run_tenant_time",
            "organization_id",
            "started_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    policy_id: Mapped[str] = mapped_column(ForeignKey("automation_policies.id"), index=True)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[AutomationPolicyRunStatus] = mapped_column(
        Enum(
            AutomationPolicyRunStatus,
            native_enum=False,
            validate_strings=True,
            create_constraint=True,
            name="automation_policy_runs_status",
        ),
        default=AutomationPolicyRunStatus.RUNNING,
    )
    stop_reason: Mapped[str | None] = mapped_column(String(100))
    executed_steps: Mapped[Any] = mapped_column(JSON, default=list)
    error_code: Mapped[str | None] = mapped_column(String(100))
    run_as_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
