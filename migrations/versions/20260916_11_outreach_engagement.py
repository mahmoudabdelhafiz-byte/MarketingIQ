"""Append-only outreach engagement tracking.

Revision ID: 20260916_11
Revises: 20260916_10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260916_11"
down_revision: str | None = "20260916_10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "outreach_engagement_events" in existing:
        return

    op.create_table(
        "outreach_engagement_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column(
            "organization_company_id",
            sa.String(36),
            sa.ForeignKey("organization_companies.id"),
            nullable=False,
        ),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column(
            "send_attempt_id",
            sa.String(36),
            sa.ForeignKey("outbound_send_attempts.id"),
            nullable=False,
        ),
        sa.Column("draft_id", sa.String(36), sa.ForeignKey("campaign_drafts.id"), nullable=False),
        sa.Column(
            "contact_id",
            sa.String(36),
            sa.ForeignKey("contact_candidates.id"),
            nullable=False,
        ),
        sa.Column(
            "contact_email_id",
            sa.String(36),
            sa.ForeignKey("contact_emails.id"),
            nullable=False,
        ),
        sa.Column("event_key", sa.String(100), nullable=False),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("reason_code", sa.String(100), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_by_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "organization_id",
            "event_key",
            name="uq_outreach_engagement_org_event_key",
        ),
    )
    for column in (
        "organization_id",
        "organization_company_id",
        "company_id",
        "send_attempt_id",
        "draft_id",
        "contact_id",
        "contact_email_id",
        "recorded_by_user_id",
    ):
        op.create_index(
            f"ix_outreach_engagement_events_{column}",
            "outreach_engagement_events",
            [column],
        )
    op.create_index(
        "ix_outreach_engagement_tenant_attempt_time",
        "outreach_engagement_events",
        ["organization_id", "send_attempt_id", "occurred_at"],
    )


def downgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "outreach_engagement_events" in existing:
        op.drop_table("outreach_engagement_events")
