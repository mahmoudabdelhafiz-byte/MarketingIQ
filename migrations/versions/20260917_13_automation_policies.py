"""Scheduled automation policies and cron-safe execution history.

Revision ID: 20260917_13
Revises: 20260916_12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260917_13"
down_revision: str | None = "20260916_12"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "automation_policies" not in existing:
        op.create_table(
            "automation_policies",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "organization_id",
                sa.String(36),
                sa.ForeignKey("organizations.id"),
                nullable=False,
            ),
            sa.Column(
                "organization_company_id",
                sa.String(36),
                sa.ForeignKey("organization_companies.id"),
                nullable=False,
            ),
            sa.Column(
                "product_id",
                sa.String(36),
                sa.ForeignKey("products.id"),
                nullable=False,
            ),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("cadence_minutes", sa.Integer(), nullable=False),
            sa.Column("allow_provider_credits", sa.Boolean(), nullable=False),
            sa.Column("contact_provider", sa.String(100), nullable=False),
            sa.Column("max_contacts", sa.Integer(), nullable=False),
            sa.Column("max_steps", sa.Integer(), nullable=False),
            sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_status", sa.String(30), nullable=True),
            sa.Column("last_error_code", sa.String(100), nullable=True),
            sa.Column(
                "run_as_user_id",
                sa.String(36),
                sa.ForeignKey("users.id"),
                nullable=False,
            ),
            sa.Column(
                "created_by_user_id",
                sa.String(36),
                sa.ForeignKey("users.id"),
                nullable=False,
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "organization_id",
                "organization_company_id",
                "product_id",
                name="uq_automation_policy_scope",
            ),
            sa.CheckConstraint(
                "cadence_minutes >= 60 AND cadence_minutes <= 43200",
                name="ck_automation_policy_cadence",
            ),
            sa.CheckConstraint(
                "max_contacts >= 1 AND max_contacts <= 50",
                name="ck_automation_policy_max_contacts",
            ),
            sa.CheckConstraint(
                "max_steps >= 1 AND max_steps <= 4",
                name="ck_automation_policy_max_steps",
            ),
        )
        for column in (
            "organization_id",
            "organization_company_id",
            "product_id",
            "next_run_at",
            "run_as_user_id",
        ):
            op.create_index(
                f"ix_automation_policies_{column}",
                "automation_policies",
                [column],
            )
        op.create_index(
            "ix_automation_policy_due",
            "automation_policies",
            ["organization_id", "enabled", "next_run_at"],
        )

    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "automation_policy_runs" not in existing:
        op.create_table(
            "automation_policy_runs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "organization_id",
                sa.String(36),
                sa.ForeignKey("organizations.id"),
                nullable=False,
            ),
            sa.Column(
                "policy_id",
                sa.String(36),
                sa.ForeignKey("automation_policies.id"),
                nullable=False,
            ),
            sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("status", sa.String(30), nullable=False),
            sa.Column("stop_reason", sa.String(100), nullable=True),
            sa.Column("executed_steps", sa.JSON(), nullable=False),
            sa.Column("error_code", sa.String(100), nullable=True),
            sa.Column(
                "run_as_user_id",
                sa.String(36),
                sa.ForeignKey("users.id"),
                nullable=False,
            ),
            sa.Column(
                "triggered_by_user_id",
                sa.String(36),
                sa.ForeignKey("users.id"),
                nullable=True,
            ),
            sa.UniqueConstraint(
                "policy_id",
                "scheduled_for",
                name="uq_automation_policy_run_schedule",
            ),
        )
        for column in (
            "organization_id",
            "policy_id",
            "run_as_user_id",
            "triggered_by_user_id",
        ):
            op.create_index(
                f"ix_automation_policy_runs_{column}",
                "automation_policy_runs",
                [column],
            )
        op.create_index(
            "ix_automation_policy_run_tenant_time",
            "automation_policy_runs",
            ["organization_id", "started_at"],
        )


def downgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "automation_policy_runs" in existing:
        op.drop_table("automation_policy_runs")
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "automation_policies" in existing:
        op.drop_table("automation_policies")
