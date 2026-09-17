"""Make targeting criterion values compatible with MySQL unique keys.

Revision ID: 20260918_18
Revises: 20260917_17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260918_18"
down_revision: str | None = "20260917_17"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CRITERIA_TABLES = ("product_criteria", "icp_criteria")


def _value_column(inspector: sa.Inspector, table_name: str) -> dict:
    return next(column for column in inspector.get_columns(table_name) if column["name"] == "value")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    for table_name in CRITERIA_TABLES:
        if table_name not in existing_tables:
            continue
        column = _value_column(inspector, table_name)
        if getattr(column["type"], "length", None) == 500:
            continue
        with op.batch_alter_table(table_name) as batch:
            batch.alter_column(
                "value",
                existing_type=column["type"],
                type_=sa.String(500),
                existing_nullable=bool(column["nullable"]),
            )
        inspector = sa.inspect(bind)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "mysql":
        raise RuntimeError(
            "Cannot downgrade criterion values to TEXT on MySQL because they participate "
            "in unique keys."
        )

    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())
    for table_name in CRITERIA_TABLES:
        if table_name not in existing_tables:
            continue
        column = _value_column(inspector, table_name)
        if isinstance(column["type"], sa.Text):
            continue
        with op.batch_alter_table(table_name) as batch:
            batch.alter_column(
                "value",
                existing_type=column["type"],
                type_=sa.Text(),
                existing_nullable=bool(column["nullable"]),
            )
        inspector = sa.inspect(bind)
