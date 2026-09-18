"""Durable shared-mailbox IMAP ingestion checkpoint.

Revision ID: 20260917_17
Revises: 20260917_16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260917_17"
down_revision: str | None = "20260917_16"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "mailbox_engagement_checkpoints" in inspector.get_table_names():
        return

    op.create_table(
        "mailbox_engagement_checkpoints",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("mailbox_identity", sa.String(64), nullable=False),
        sa.Column("uid_validity", sa.String(64), nullable=False),
        sa.Column("last_processed_uid", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "mailbox_identity",
            name="uq_mailbox_engagement_checkpoint_identity",
        ),
    )
    op.create_index(
        "ix_mailbox_engagement_checkpoint_identity",
        "mailbox_engagement_checkpoints",
        ["mailbox_identity"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "mailbox_engagement_checkpoints" not in inspector.get_table_names():
        return
    indexes = {
        item["name"] for item in inspector.get_indexes("mailbox_engagement_checkpoints")
    }
    if "ix_mailbox_engagement_checkpoint_identity" in indexes:
        op.drop_index(
            "ix_mailbox_engagement_checkpoint_identity",
            table_name="mailbox_engagement_checkpoints",
        )
    op.drop_table("mailbox_engagement_checkpoints")
