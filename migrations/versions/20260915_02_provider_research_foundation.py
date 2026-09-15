"""Provider research foundation.

Revision ID: 20260915_02
Revises: 20260914_01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260915_02"
down_revision: str | None = "20260914_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The Sprint 1 bootstrap revision used live metadata. The guards support both databases that
    # already ran Sprint 1 and clean installs where that bootstrap sees the current model snapshot.
    inspector = sa.inspect(op.get_bind())
    run_columns = {column["name"] for column in inspector.get_columns("research_runs")}
    if "mode" not in run_columns:
        with op.batch_alter_table("research_runs") as batch:
            batch.drop_constraint("researchstatus", type_="check")
            batch.create_check_constraint(
                "researchstatus",
                "status IN ('PENDING', 'RUNNING', 'COMPLETED', 'PARTIAL', 'FAILED')",
            )
            batch.add_column(
                sa.Column("mode", sa.String(20), nullable=False, server_default="PUBLIC_ONLY")
            )
            batch.add_column(
                sa.Column("providers_attempted", sa.JSON(), nullable=False, server_default="[]")
            )
            batch.add_column(
                sa.Column("providers_succeeded", sa.JSON(), nullable=False, server_default="[]")
            )
            batch.add_column(sa.Column("error_summary", sa.Text(), nullable=True))
            batch.add_column(sa.Column("workflow_version", sa.String(100), nullable=True))
    if "provider_usage" in inspector.get_table_names():
        return
    op.create_table(
        "provider_usage",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("provider_key", sa.String(100), nullable=False, index=True),
        sa.Column(
            "organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False
        ),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("operation", sa.String(100), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("response_status", sa.String(50), nullable=False),
        sa.Column("credits_used", sa.Integer()),
        sa.Column("estimated_cost", sa.Numeric(12, 4)),
        sa.Column("credits_remaining", sa.Integer()),
        sa.Column("request_identifier", sa.String(255)),
        sa.Column("error_category", sa.String(100)),
        sa.Column("cache_hit", sa.Boolean(), nullable=False),
    )
    op.create_index(
        "ix_provider_usage_org_requested", "provider_usage", ["organization_id", "requested_at"]
    )
    op.create_index("ix_provider_usage_company_id", "provider_usage", ["company_id"])


def downgrade() -> None:
    op.drop_table("provider_usage")
    with op.batch_alter_table("research_runs") as batch:
        for column in (
            "workflow_version",
            "error_summary",
            "providers_succeeded",
            "providers_attempted",
            "mode",
        ):
            batch.drop_column(column)
