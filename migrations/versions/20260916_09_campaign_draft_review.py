"""Append-only campaign draft review and edit history.

Revision ID: 20260916_09
Revises: 20260915_08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260916_09"
down_revision: str | None = "20260915_08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if "campaign_draft_review_events" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "campaign_draft_review_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False
        ),
        sa.Column(
            "draft_id", sa.String(36), sa.ForeignKey("campaign_drafts.id"), nullable=False
        ),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("call_to_action", sa.String(500), nullable=False),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.Column("created_by_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("draft_id", "revision_number", name="uq_campaign_review_revision"),
    )
    op.create_index(
        "ix_campaign_draft_review_events_organization_id",
        "campaign_draft_review_events",
        ["organization_id"],
    )
    op.create_index(
        "ix_campaign_draft_review_events_draft_id",
        "campaign_draft_review_events",
        ["draft_id"],
    )
    op.create_index(
        "ix_campaign_draft_review_events_created_by_user_id",
        "campaign_draft_review_events",
        ["created_by_user_id"],
    )
    op.create_index(
        "ix_campaign_review_tenant_draft_time",
        "campaign_draft_review_events",
        ["organization_id", "draft_id", "created_at"],
    )


def downgrade() -> None:
    if "campaign_draft_review_events" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("campaign_draft_review_events")
