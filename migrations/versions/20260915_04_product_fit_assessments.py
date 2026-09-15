"""Product/ICP fit assessment history and criterion weights.

Revision ID: 20260915_04
Revises: 20260915_03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260915_04"
down_revision: str | None = "20260915_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("icp_criteria") as batch:
        batch.add_column(sa.Column("weight", sa.Integer(), nullable=False, server_default="3"))
        batch.add_column(
            sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    op.create_table(
        "product_fit_assessments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False
        ),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column(
            "organization_company_id",
            sa.String(36),
            sa.ForeignKey("organization_companies.id"),
            nullable=False,
        ),
        sa.Column("product_id", sa.String(36), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("icp_id", sa.String(36), sa.ForeignKey("icps.id"), nullable=False),
        sa.Column("icp_version", sa.Integer(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("evidence_coverage", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.String(20), nullable=False),
        sa.Column("grade", sa.String(20), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("workflow_version", sa.String(100), nullable=False),
        sa.Column("evidence_snapshot", sa.JSON(), nullable=False),
        sa.Column("explanation", sa.JSON(), nullable=False),
        sa.Column("classification", sa.String(30), nullable=False),
        sa.Column("created_by_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.CheckConstraint("score >= 0 AND score <= 100", name="ck_fit_score"),
        sa.CheckConstraint(
            "evidence_coverage >= 0 AND evidence_coverage <= 100", name="ck_fit_coverage"
        ),
    )
    for column in (
        "organization_id",
        "company_id",
        "organization_company_id",
        "product_id",
        "icp_id",
    ):
        op.create_index(f"ix_product_fit_assessments_{column}", "product_fit_assessments", [column])
    op.create_index(
        "ix_fit_tenant_relationship_time",
        "product_fit_assessments",
        ["organization_id", "organization_company_id", "evaluated_at"],
    )


def downgrade() -> None:
    op.drop_table("product_fit_assessments")
    with op.batch_alter_table("icp_criteria") as batch:
        batch.drop_column("required")
        batch.drop_column("weight")
