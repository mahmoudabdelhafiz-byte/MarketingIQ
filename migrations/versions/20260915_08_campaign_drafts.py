"""Tenant-private campaign draft generation.

Revision ID: 20260915_08
Revises: 20260915_07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260915_08"
down_revision: str | None = "20260915_07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if "campaign_drafts" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "campaign_drafts",
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
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("call_to_action", sa.String(500), nullable=False),
        sa.Column("message_angle", sa.String(100), nullable=False),
        sa.Column("personalization_snapshot", sa.JSON(), nullable=False),
        sa.Column("evidence_snapshot", sa.JSON(), nullable=False),
        sa.Column("workflow_version", sa.String(100), nullable=False),
        sa.Column("created_by_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("classification", sa.String(30), nullable=False),
        sa.Column("redistribution_status", sa.String(20), nullable=False),
    )
    for col in (
        "organization_id",
        "organization_company_id",
        "company_id",
        "product_id",
        "qualification_id",
        "contact_id",
    ):
        op.create_index(f"ix_campaign_drafts_{col}", "campaign_drafts", [col])
    op.create_index(
        "ix_campaign_draft_tenant_relationship_time",
        "campaign_drafts",
        ["organization_id", "organization_company_id", "created_at"],
    )


def downgrade() -> None:
    if "campaign_drafts" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("campaign_drafts")
