"""Automatic outreach-to-pipeline synchronization metadata.

Revision ID: 20260917_16
Revises: 20260917_15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260917_16"
down_revision: str | None = "20260917_15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "sales_opportunity_stage_events" not in tables:
        return

    columns = {
        item["name"]: item for item in inspector.get_columns("sales_opportunity_stage_events")
    }
    if "source" not in columns:
        op.add_column(
            "sales_opportunity_stage_events",
            sa.Column(
                "source",
                sa.String(20),
                nullable=False,
                server_default="MANUAL",
            ),
        )
        op.alter_column(
            "sales_opportunity_stage_events",
            "source",
            existing_type=sa.String(20),
            server_default=None,
        )

    inspector = sa.inspect(bind)
    columns = {
        item["name"]: item for item in inspector.get_columns("sales_opportunity_stage_events")
    }
    if not columns.get("created_by_user_id", {}).get("nullable", True):
        op.alter_column(
            "sales_opportunity_stage_events",
            "created_by_user_id",
            existing_type=sa.String(36),
            nullable=True,
        )

    check_names = {
        item.get("name") for item in inspector.get_check_constraints("sales_opportunity_stage_events")
    }
    if "sales_opportunity_stage_event_source" not in check_names:
        op.create_check_constraint(
            "sales_opportunity_stage_event_source",
            "sales_opportunity_stage_events",
            "source IN ('MANUAL', 'SYSTEM')",
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "sales_opportunity_stage_events" not in tables:
        return

    op.execute(
        sa.text(
            "UPDATE sales_opportunity_stage_events "
            "SET created_by_user_id = ("
            "SELECT created_by_user_id FROM sales_opportunities "
            "WHERE sales_opportunities.id = sales_opportunity_stage_events.opportunity_id"
            ") WHERE created_by_user_id IS NULL"
        )
    )

    columns = {
        item["name"]: item for item in inspector.get_columns("sales_opportunity_stage_events")
    }
    if columns.get("created_by_user_id", {}).get("nullable", False):
        op.alter_column(
            "sales_opportunity_stage_events",
            "created_by_user_id",
            existing_type=sa.String(36),
            nullable=False,
        )

    check_names = {
        item.get("name") for item in inspector.get_check_constraints("sales_opportunity_stage_events")
    }
    if "sales_opportunity_stage_event_source" in check_names:
        op.drop_constraint(
            "sales_opportunity_stage_event_source",
            "sales_opportunity_stage_events",
            type_="check",
        )

    columns = {item["name"] for item in inspector.get_columns("sales_opportunity_stage_events")}
    if "source" in columns:
        op.drop_column("sales_opportunity_stage_events", "source")
