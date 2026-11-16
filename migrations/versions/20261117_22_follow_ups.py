"""Follow-up emails sent in the original thread when a prospect has not replied.

Revision ID: 20261117_22
Revises: 20260923_21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261117_22"
down_revision: str | None = "20260923_21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "outbound_follow_ups"


def upgrade() -> None:
    if TABLE in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        TABLE,
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
        sa.Column(
            "initial_attempt_id",
            sa.String(36),
            sa.ForeignKey("outbound_send_attempts.id"),
            nullable=False,
        ),
        sa.Column("draft_id", sa.String(36), sa.ForeignKey("campaign_drafts.id"), nullable=False),
        sa.Column(
            "contact_id", sa.String(36), sa.ForeignKey("contact_candidates.id"), nullable=False
        ),
        sa.Column(
            "contact_email_id", sa.String(36), sa.ForeignKey("contact_emails.id"), nullable=False
        ),
        sa.Column("step", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(17), nullable=False),
        sa.Column("auto_send", sa.Boolean(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_by_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("provider_key", sa.String(50), nullable=True),
        sa.Column("provider_message_id", sa.String(255), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stop_reason", sa.String(50), nullable=True),
        sa.UniqueConstraint("initial_attempt_id", "step", name="uq_follow_up_attempt_step"),
        sa.CheckConstraint(
            "status IN ('SCHEDULED', 'AWAITING_APPROVAL', 'SENT', 'SKIPPED', 'FAILED')",
            name="outbound_follow_up_status",
        ),
        sa.CheckConstraint("step >= 1 AND step <= 2", name="ck_outbound_follow_up_step"),
    )
    for column in (
        "organization_id",
        "organization_company_id",
        "company_id",
        "initial_attempt_id",
        "draft_id",
        "contact_id",
        "provider_message_id",
    ):
        op.create_index(f"ix_{TABLE}_{column}", TABLE, [column])
    op.create_index("ix_follow_up_status_due", TABLE, ["status", "due_at"])
    op.create_index("ix_follow_up_org_status", TABLE, ["organization_id", "status"])


def downgrade() -> None:
    if TABLE in set(sa.inspect(op.get_bind()).get_table_names()):
        op.drop_table(TABLE)
