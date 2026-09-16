"""Sales opportunity pipeline foundation.

Revision ID: 20260916_12
Revises: 20260916_11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260916_12"
down_revision: str | None = "20260916_11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "sales_opportunities" not in existing:
        op.create_table(
            "sales_opportunities",
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
                "company_id",
                sa.String(36),
                sa.ForeignKey("companies.id"),
                nullable=False,
            ),
            sa.Column(
                "product_id",
                sa.String(36),
                sa.ForeignKey("products.id"),
                nullable=False,
            ),
            sa.Column(
                "qualification_id",
                sa.String(36),
                sa.ForeignKey("lead_qualifications.id"),
                nullable=False,
            ),
            sa.Column(
                "contact_id",
                sa.String(36),
                sa.ForeignKey("contact_candidates.id"),
                nullable=False,
            ),
            sa.Column(
                "draft_id",
                sa.String(36),
                sa.ForeignKey("campaign_drafts.id"),
                nullable=False,
            ),
            sa.Column(
                "send_attempt_id",
                sa.String(36),
                sa.ForeignKey("outbound_send_attempts.id"),
                nullable=False,
            ),
            sa.Column("stage", sa.String(20), nullable=False),
            sa.Column("estimated_value", sa.Numeric(14, 2), nullable=True),
            sa.Column("currency", sa.String(3), nullable=True),
            sa.Column("next_action", sa.String(500), nullable=True),
            sa.Column(
                "owner_user_id",
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
                "send_attempt_id",
                name="uq_sales_opportunity_org_send_attempt",
            ),
        )
        for column in (
            "organization_id",
            "organization_company_id",
            "company_id",
            "product_id",
            "qualification_id",
            "contact_id",
            "draft_id",
            "send_attempt_id",
            "owner_user_id",
            "created_by_user_id",
        ):
            op.create_index(
                f"ix_sales_opportunities_{column}",
                "sales_opportunities",
                [column],
            )
        op.create_index(
            "ix_sales_opportunity_tenant_stage_time",
            "sales_opportunities",
            ["organization_id", "stage", "updated_at"],
        )

    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "sales_opportunity_stage_events" not in existing:
        op.create_table(
            "sales_opportunity_stage_events",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "organization_id",
                sa.String(36),
                sa.ForeignKey("organizations.id"),
                nullable=False,
            ),
            sa.Column(
                "opportunity_id",
                sa.String(36),
                sa.ForeignKey("sales_opportunities.id"),
                nullable=False,
            ),
            sa.Column("sequence_number", sa.Integer(), nullable=False),
            sa.Column("from_stage", sa.String(20), nullable=True),
            sa.Column("to_stage", sa.String(20), nullable=False),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("reason_code", sa.String(100), nullable=True),
            sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column(
                "created_by_user_id",
                sa.String(36),
                sa.ForeignKey("users.id"),
                nullable=False,
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "opportunity_id",
                "sequence_number",
                name="uq_sales_opportunity_stage_sequence",
            ),
        )
        for column in (
            "organization_id",
            "opportunity_id",
            "created_by_user_id",
        ):
            op.create_index(
                f"ix_sales_opportunity_stage_events_{column}",
                "sales_opportunity_stage_events",
                [column],
            )
        op.create_index(
            "ix_sales_opportunity_stage_event_tenant_time",
            "sales_opportunity_stage_events",
            ["organization_id", "occurred_at"],
        )


def downgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "sales_opportunity_stage_events" in existing:
        op.drop_table("sales_opportunity_stage_events")
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "sales_opportunities" in existing:
        op.drop_table("sales_opportunities")
