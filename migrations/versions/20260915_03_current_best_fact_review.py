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
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("human_overrides")}
    with op.batch_alter_table("human_overrides") as batch:
        if "fact_key" not in columns:
            batch.add_column(sa.Column("fact_key", sa.String(150), nullable=True))
        if "action" not in columns:
            batch.add_column(sa.Column("action", sa.String(30), nullable=True))
        if "revoked_at" not in columns:
            batch.add_column(sa.Column("revoked_at", sa.DateTime(timezone=True)))
        if "revoked_by_user_id" not in columns:
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
        # MySQL CHANGE/MODIFY COLUMN requires the existing type to be supplied explicitly.
        batch.alter_column("fact_key", existing_type=sa.String(150), nullable=False)
        batch.alter_column("action", existing_type=sa.String(30), nullable=False)

    # Clean installs may already contain this index because the legacy bootstrap migration imports
    # live metadata. Guard index creation so this revision remains safe for both upgrade paths.
    index_names = {
        index["name"] for index in sa.inspect(bind).get_indexes("human_overrides") if index["name"]
    }
    if "ix_human_overrides_fact_key" not in index_names:
        op.create_index("ix_human_overrides_fact_key", "human_overrides", ["fact_key"])


def downgrade() -> None:
    bind = op.get_bind()
    index_names = {
        index["name"] for index in sa.inspect(bind).get_indexes("human_overrides") if index["name"]
    }
    if "ix_human_overrides_fact_key" in index_names:
        op.drop_index("ix_human_overrides_fact_key", table_name="human_overrides")

    columns = {column["name"] for column in sa.inspect(bind).get_columns("human_overrides")}
    with op.batch_alter_table("human_overrides") as batch:
        if "revoked_by_user_id" in columns:
            batch.drop_column("revoked_by_user_id")
        if "revoked_at" in columns:
            batch.drop_column("revoked_at")
        if "action" in columns:
            batch.drop_column("action")
        if "fact_key" in columns:
            batch.drop_column("fact_key")
