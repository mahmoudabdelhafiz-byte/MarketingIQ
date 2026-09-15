"""Lead qualifications and configurable Product buyer roles.

Revision ID: 20260915_06
Revises: 20260915_05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260915_06"
down_revision: str | None = "20260915_05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    product_columns = {column["name"] for column in inspector.get_columns("products")}
    missing_roles = [
        name
        for name in ("primary_buyer_roles", "secondary_buyer_roles")
        if name not in product_columns
    ]
    if missing_roles:
        with op.batch_alter_table("products") as batch:
            for name in missing_roles:
                batch.add_column(sa.Column(name, sa.JSON(), nullable=True))
        for name in missing_roles:
            op.execute(f"UPDATE products SET {name}='[]'")
        with op.batch_alter_table("products") as batch:
            for name in missing_roles:
                batch.alter_column(name, nullable=False)

    if "lead_qualifications" in inspector.get_table_names():
        return

    op.create_table(
        "lead_qualifications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False
        ),
        sa.Column(
            "organization_company_id",
            sa.String(36),
            sa.ForeignKey("organization_companies.id"),
            nullable=False,
        ),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("product_id", sa.String(36), sa.ForeignKey("products.id"), nullable=False),
        sa.Column(
            "fit_assessment_id",
            sa.String(36),
            sa.ForeignKey("product_fit_assessments.id"),
            nullable=False,
        ),
        sa.Column("qualification_score", sa.Integer(), nullable=False),
        sa.Column("qualification_grade", sa.String(7), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("confidence", sa.String(20), nullable=False),
        sa.Column("recommended_buyer_role", sa.String(200), nullable=False),
        sa.Column("buyer_role_confidence", sa.String(20), nullable=False),
        sa.Column("alternative_buyer_roles", sa.JSON(), nullable=False),
        sa.Column("reasons", sa.JSON(), nullable=False),
        sa.Column("research_gaps", sa.JSON(), nullable=False),
        sa.Column("component_scores", sa.JSON(), nullable=False),
        sa.Column("workflow_version", sa.String(100), nullable=False),
        sa.Column("qualified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("classification", sa.String(30), nullable=False),
        sa.CheckConstraint(
            "qualification_score >= 0 AND qualification_score <= 100", name="ck_qualification_score"
        ),
    )
    for column in (
        "organization_id",
        "organization_company_id",
        "company_id",
        "product_id",
        "fit_assessment_id",
    ):
        op.create_index(f"ix_lead_qualifications_{column}", "lead_qualifications", [column])
    op.create_index(
        "ix_qualification_tenant_relationship_time",
        "lead_qualifications",
        ["organization_id", "organization_company_id", "qualified_at"],
    )


def downgrade() -> None:
    op.drop_table("lead_qualifications")
    with op.batch_alter_table("products") as batch:
        batch.drop_column("secondary_buyer_roles")
        batch.drop_column("primary_buyer_roles")
