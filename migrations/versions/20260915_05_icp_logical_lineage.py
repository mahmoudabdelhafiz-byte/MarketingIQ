"""Explicit logical lineage for immutable ICP revisions.

Revision ID: 20260915_05
Revises: 20260915_04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260915_05"
down_revision: str | None = "20260915_04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("icps")}
    if "logical_id" not in columns:
        with op.batch_alter_table("icps") as batch:
            batch.add_column(sa.Column("logical_id", sa.String(36), nullable=True))
            batch.create_index("ix_icps_logical_id", ["logical_id"])


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("icps")}
    if "logical_id" in columns:
        with op.batch_alter_table("icps") as batch:
            batch.drop_index("ix_icps_logical_id")
            batch.drop_column("logical_id")
