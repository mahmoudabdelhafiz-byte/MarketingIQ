"""Explicit outbound sending and suppression safeguards.

Revision ID: 20260916_10
Revises: 20260916_09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260916_10"
down_revision: str | None = "20260916_09"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())

    if "outbound_suppressions" not in existing:
        op.create_table(
            "outbound_suppressions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "organization_id",
                sa.String(36),
                sa.ForeignKey("organizations.id"),
                nullable=False,
            ),
            sa.Column("email_normalized", sa.String(320), nullable=False),
            sa.Column("source", sa.String(20), nullable=False),
            sa.Column("reason", sa.String(500), nullable=True),
            sa.Column(
                "created_by_user_id",
                sa.String(36),
                sa.ForeignKey("users.id"),
                nullable=False,
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "organization_id",
                "email_normalized",
                name="uq_outbound_suppression_org_email",
            ),
        )
        op.create_index(
            "ix_outbound_suppressions_organization_id",
            "outbound_suppressions",
            ["organization_id"],
        )
        op.create_index(
            "ix_outbound_suppressions_created_by_user_id",
            "outbound_suppressions",
            ["created_by_user_id"],
        )
        op.create_index(
            "ix_outbound_suppression_tenant_time",
            "outbound_suppressions",
            ["organization_id", "created_at"],
        )

    if "outbound_send_attempts" not in existing:
        op.create_table(
            "outbound_send_attempts",
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
            sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
            sa.Column(
                "draft_id", sa.String(36), sa.ForeignKey("campaign_drafts.id"), nullable=False
            ),
            sa.Column(
                "review_event_id",
                sa.String(36),
                sa.ForeignKey("campaign_draft_review_events.id"),
                nullable=False,
            ),
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
            sa.Column("provider_key", sa.String(50), nullable=False),
            sa.Column("idempotency_key", sa.String(100), nullable=False),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("provider_message_id", sa.String(255), nullable=True),
            sa.Column("error_category", sa.String(50), nullable=True),
            sa.Column(
                "requested_by_user_id",
                sa.String(36),
                sa.ForeignKey("users.id"),
                nullable=False,
            ),
            sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint(
                "organization_id",
                "idempotency_key",
                name="uq_outbound_send_org_idempotency",
            ),
        )
        for column in (
            "organization_id",
            "organization_company_id",
            "company_id",
            "draft_id",
            "review_event_id",
            "contact_id",
            "contact_email_id",
            "requested_by_user_id",
        ):
            op.create_index(
                f"ix_outbound_send_attempts_{column}",
                "outbound_send_attempts",
                [column],
            )
        op.create_index(
            "ix_outbound_send_tenant_draft_time",
            "outbound_send_attempts",
            ["organization_id", "draft_id", "requested_at"],
        )


def downgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "outbound_send_attempts" in existing:
        op.drop_table("outbound_send_attempts")
    if "outbound_suppressions" in existing:
        op.drop_table("outbound_suppressions")
