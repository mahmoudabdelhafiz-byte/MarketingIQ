"""Unit prices for the cost dashboard.

Revision ID: 20261119_24
Revises: 20261118_23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261119_24"
down_revision: str | None = "20261118_23"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "organization_cost_settings"


def upgrade() -> None:
    if TABLE in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False
        ),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("provider_credit_cost", sa.Numeric(12, 4), nullable=True),
        sa.Column("ai_input_per_million", sa.Numeric(12, 4), nullable=True),
        sa.Column("ai_output_per_million", sa.Numeric(12, 4), nullable=True),
        sa.Column("web_search_cost", sa.Numeric(12, 4), nullable=True),
        sa.Column("monthly_fixed_cost", sa.Numeric(12, 2), nullable=True),
        sa.Column("updated_by_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        f"ix_{TABLE}_organization_id", TABLE, ["organization_id"], unique=True
    )


def downgrade() -> None:
    if TABLE in set(sa.inspect(op.get_bind()).get_table_names()):
        op.drop_table(TABLE)
