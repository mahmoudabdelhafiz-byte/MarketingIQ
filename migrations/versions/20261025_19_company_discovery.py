"""Company discovery runs and human-reviewed discovery candidates.

Revision ID: 20261025_19
Revises: 20260918_18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261025_19"
down_revision: str | None = "20260918_18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "company_discovery_runs" not in existing:
        op.create_table(
            "company_discovery_runs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "organization_id",
                sa.String(36),
                sa.ForeignKey("organizations.id"),
                nullable=False,
            ),
            sa.Column("product_id", sa.String(36), sa.ForeignKey("products.id"), nullable=False),
            sa.Column("icp_id", sa.String(36), sa.ForeignKey("icps.id"), nullable=False),
            sa.Column("icp_version", sa.Integer(), nullable=False),
            sa.Column("provider_key", sa.String(100), nullable=False),
            sa.Column("model", sa.String(100), nullable=True),
            sa.Column("workflow_version", sa.String(100), nullable=False),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("requested_max_results", sa.Integer(), nullable=False),
            sa.Column("excluded_domain_count", sa.Integer(), nullable=False),
            sa.Column("returned_count", sa.Integer(), nullable=False),
            sa.Column("proposed_count", sa.Integer(), nullable=False),
            sa.Column("skipped_count", sa.Integer(), nullable=False),
            sa.Column("searches_used", sa.Integer(), nullable=True),
            sa.Column("input_tokens", sa.Integer(), nullable=True),
            sa.Column("output_tokens", sa.Integer(), nullable=True),
            sa.Column("error_category", sa.String(100), nullable=True),
            sa.Column(
                "initiated_by_user_id",
                sa.String(36),
                sa.ForeignKey("users.id"),
                nullable=False,
            ),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.CheckConstraint(
                "requested_max_results >= 1 AND requested_max_results <= 25",
                name="ck_discovery_run_max_results",
            ),
        )
        for column in ("organization_id", "product_id"):
            op.create_index(
                f"ix_company_discovery_runs_{column}", "company_discovery_runs", [column]
            )
        op.create_index(
            "ix_discovery_run_org_started",
            "company_discovery_runs",
            ["organization_id", "started_at"],
        )

    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "company_discovery_candidates" not in existing:
        op.create_table(
            "company_discovery_candidates",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "organization_id",
                sa.String(36),
                sa.ForeignKey("organizations.id"),
                nullable=False,
            ),
            sa.Column(
                "run_id",
                sa.String(36),
                sa.ForeignKey("company_discovery_runs.id"),
                nullable=False,
            ),
            sa.Column("product_id", sa.String(36), sa.ForeignKey("products.id"), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("company_name", sa.String(255), nullable=False),
            sa.Column("raw_domain", sa.String(255), nullable=False),
            sa.Column("normalized_domain", sa.String(255), nullable=True),
            sa.Column("country_code", sa.String(2), nullable=True),
            sa.Column("industry", sa.String(200), nullable=True),
            sa.Column("rationale", sa.Text(), nullable=True),
            sa.Column("source_urls", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("skip_reason", sa.String(30), nullable=True),
            sa.Column("classification", sa.String(30), nullable=False),
            sa.Column(
                "organization_company_id",
                sa.String(36),
                sa.ForeignKey("organization_companies.id"),
                nullable=True,
            ),
            sa.Column(
                "reviewed_by_user_id",
                sa.String(36),
                sa.ForeignKey("users.id"),
                nullable=True,
            ),
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("review_note", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        for column in ("organization_id", "run_id", "product_id"):
            op.create_index(
                f"ix_company_discovery_candidates_{column}",
                "company_discovery_candidates",
                [column],
            )
        op.create_index(
            "ix_discovery_candidate_org_domain",
            "company_discovery_candidates",
            ["organization_id", "normalized_domain"],
        )
        op.create_index(
            "ix_discovery_candidate_org_status",
            "company_discovery_candidates",
            ["organization_id", "status"],
        )


def downgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "company_discovery_candidates" in existing:
        op.drop_table("company_discovery_candidates")
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "company_discovery_runs" in existing:
        op.drop_table("company_discovery_runs")
