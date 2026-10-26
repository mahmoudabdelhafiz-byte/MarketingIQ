"""Scheduled company discovery policies and runs.

Revision ID: 20260923_20
Revises: 20260923_19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260923_20"
down_revision: str | None = "20260923_19"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICIES = "company_discovery_policies"
RUNS = "company_discovery_runs"


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())

    if POLICIES not in tables:
        op.create_table(
            POLICIES,
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("organization_id", sa.String(36), nullable=False),
            sa.Column("product_id", sa.String(36), nullable=False),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("provider_key", sa.String(50), nullable=False),
            sa.Column("query", sa.String(500), nullable=True),
            sa.Column("countries", sa.JSON(), nullable=True),
            sa.Column("industries", sa.JSON(), nullable=True),
            sa.Column("keywords", sa.JSON(), nullable=True),
            sa.Column("headcounts", sa.JSON(), nullable=True),
            sa.Column("max_new_per_run", sa.Integer(), nullable=False),
            sa.Column("cadence_minutes", sa.Integer(), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("run_workflow", sa.Boolean(), nullable=False),
            sa.Column("allow_provider_credits", sa.Boolean(), nullable=False),
            sa.Column("next_offset", sa.Integer(), nullable=False),
            sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_status", sa.String(30), nullable=True),
            sa.Column("last_error_code", sa.String(100), nullable=True),
            sa.Column("run_as_user_id", sa.String(36), nullable=False),
            sa.Column("created_by_user_id", sa.String(36), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
            sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
            sa.ForeignKeyConstraint(["run_as_user_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
            sa.CheckConstraint(
                "cadence_minutes >= 60 AND cadence_minutes <= 43200",
                name="ck_company_discovery_cadence",
            ),
            sa.CheckConstraint(
                "max_new_per_run >= 1 AND max_new_per_run <= 100",
                name="ck_company_discovery_max_new",
            ),
        )
        op.create_index(
            "ix_company_discovery_due", POLICIES, ["organization_id", "enabled", "next_run_at"]
        )
        op.create_index(
            "ix_company_discovery_policies_organization_id", POLICIES, ["organization_id"]
        )
        op.create_index("ix_company_discovery_policies_product_id", POLICIES, ["product_id"])
        op.create_index("ix_company_discovery_policies_next_run_at", POLICIES, ["next_run_at"])
        op.create_index(
            "ix_company_discovery_policies_run_as_user_id", POLICIES, ["run_as_user_id"]
        )

    if RUNS not in tables:
        op.create_table(
            RUNS,
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("organization_id", sa.String(36), nullable=False),
            sa.Column("policy_id", sa.String(36), nullable=False),
            sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("status", sa.String(30), nullable=False),
            sa.Column("candidates_found", sa.Integer(), nullable=False),
            sa.Column("companies_added", sa.Integer(), nullable=False),
            sa.Column("already_known", sa.Integer(), nullable=False),
            sa.Column("workflows_completed", sa.Integer(), nullable=False),
            sa.Column("workflows_failed", sa.Integer(), nullable=False),
            sa.Column("added_relationship_ids", sa.JSON(), nullable=True),
            sa.Column("notes", sa.JSON(), nullable=True),
            sa.Column("error_code", sa.String(100), nullable=True),
            sa.Column("run_as_user_id", sa.String(36), nullable=False),
            sa.Column("triggered_by_user_id", sa.String(36), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
            sa.ForeignKeyConstraint(["policy_id"], [f"{POLICIES}.id"]),
            sa.ForeignKeyConstraint(["run_as_user_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["triggered_by_user_id"], ["users.id"]),
            sa.UniqueConstraint(
                "policy_id", "scheduled_for", name="uq_company_discovery_run_schedule"
            ),
        )
        op.create_index(
            "ix_company_discovery_run_tenant_time", RUNS, ["organization_id", "started_at"]
        )
        op.create_index("ix_company_discovery_runs_organization_id", RUNS, ["organization_id"])
        op.create_index("ix_company_discovery_runs_policy_id", RUNS, ["policy_id"])
        op.create_index("ix_company_discovery_runs_run_as_user_id", RUNS, ["run_as_user_id"])
        op.create_index(
            "ix_company_discovery_runs_triggered_by_user_id", RUNS, ["triggered_by_user_id"]
        )


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if RUNS in tables:
        op.drop_table(RUNS)
    if POLICIES in tables:
        op.drop_table(POLICIES)
