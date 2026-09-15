"""Current best fact human review state.

Revision ID: 20260915_03
Revises: 20260915_02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260915_03"
down_revision: str | None = "20260915_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("human_overrides")
    }
    with op.batch_alter_table("human_overrides") as batch:
        if "fact_key" not in columns:
            batch.add_column(sa.Column("fact_key", sa.String(150), nullable=True))
            batch.add_column(sa.Column("action", sa.String(30), nullable=True))
            batch.add_column(sa.Column("revoked_at", sa.DateTime(timezone=True)))
            batch.add_column(
                sa.Column("revoked_by_user_id", sa.String(36), sa.ForeignKey("users.id"))
            )
    # Legacy rows remain valid approvals. Backfill before enforcing required state.
    op.execute(
        "UPDATE human_overrides SET fact_key = (SELECT fact_key FROM company_facts "
        "WHERE company_facts.id = human_overrides.company_fact_id), action = 'SELECT' "
        "WHERE fact_key IS NULL"
    )
    with op.batch_alter_table("human_overrides") as batch:
        batch.alter_column("fact_key", nullable=False)
        batch.alter_column("action", nullable=False)
        batch.create_index("ix_human_overrides_fact_key", ["fact_key"])


def downgrade() -> None:
    with op.batch_alter_table("human_overrides") as batch:
        batch.drop_index("ix_human_overrides_fact_key")
        batch.drop_column("revoked_by_user_id")
        batch.drop_column("revoked_at")
        batch.drop_column("action")
        batch.drop_column("fact_key")
