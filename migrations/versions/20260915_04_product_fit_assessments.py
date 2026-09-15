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
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    criterion_columns = {column["name"] for column in inspector.get_columns("icp_criteria")}
    with op.batch_alter_table("icp_criteria") as batch:
        if "weight" not in criterion_columns:
            batch.add_column(sa.Column("weight", sa.Integer(), nullable=False, server_default="3"))
        if "required" not in criterion_columns:
            batch.add_column(
                sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.false())
            )

    table_names = set(sa.inspect(bind).get_table_names())
    if "product_fit_assessments" not in table_names:
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
            sa.Column(
                "created_by_user_id",
                sa.String(36),
                sa.ForeignKey("users.id"),
                nullable=False,
            ),
            sa.CheckConstraint("score >= 0 AND score <= 100", name="ck_fit_score"),
            sa.CheckConstraint(
                "evidence_coverage >= 0 AND evidence_coverage <= 100", name="ck_fit_coverage"
            ),
        )

    existing_indexes = {
        index["name"]
        for index in sa.inspect(bind).get_indexes("product_fit_assessments")
        if index["name"]
    }
    for column in (
        "organization_id",
        "company_id",
        "organization_company_id",
        "product_id",
        "icp_id",
    ):
        index_name = f"ix_product_fit_assessments_{column}"
        if index_name not in existing_indexes:
            op.create_index(index_name, "product_fit_assessments", [column])
    if "ix_fit_tenant_relationship_time" not in existing_indexes:
        op.create_index(
            "ix_fit_tenant_relationship_time",
            "product_fit_assessments",
            ["organization_id", "organization_company_id", "evaluated_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    table_names = set(sa.inspect(bind).get_table_names())
    if "product_fit_assessments" in table_names:
        op.drop_table("product_fit_assessments")

    criterion_columns = {column["name"] for column in sa.inspect(bind).get_columns("icp_criteria")}
    with op.batch_alter_table("icp_criteria") as batch:
        if "required" in criterion_columns:
            batch.drop_column("required")
        if "weight" in criterion_columns:
            batch.drop_column("weight")
