"""Failed-login throttling counters.

Revision ID: 20260923_19
Revises: 20260918_18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260923_19"
down_revision: str | None = "20260918_18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "auth_login_throttles"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if TABLE in inspector.get_table_names():
        return

    op.create_table(
        TABLE,
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("bucket_key", sa.String(80), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "bucket_key",
            "window_start",
            name="uq_auth_login_throttle_window",
        ),
    )
    op.create_index("ix_auth_login_throttle_window", TABLE, ["window_start"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if TABLE not in inspector.get_table_names():
        return
    indexes = {item["name"] for item in inspector.get_indexes(TABLE)}
    if "ix_auth_login_throttle_window" in indexes:
        op.drop_index("ix_auth_login_throttle_window", table_name=TABLE)
    op.drop_table(TABLE)
