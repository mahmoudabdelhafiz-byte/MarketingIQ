"""Authenticated provider engagement webhook ingestion.

Revision ID: 20260917_15
Revises: 20260917_14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260917_15"
down_revision: str | None = "20260917_14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "outreach_engagement_events" in tables:
        columns = {item["name"]: item for item in inspector.get_columns("outreach_engagement_events")}
        if "provider_key" not in columns:
            op.add_column(
                "outreach_engagement_events",
                sa.Column("provider_key", sa.String(50), nullable=True),
            )
        if "provider_event_id" not in columns:
            op.add_column(
                "outreach_engagement_events",
                sa.Column("provider_event_id", sa.String(150), nullable=True),
            )
        if not columns.get("recorded_by_user_id", {}).get("nullable", True):
            op.alter_column(
                "outreach_engagement_events",
                "recorded_by_user_id",
                existing_type=sa.String(36),
                nullable=True,
            )

        inspector = sa.inspect(bind)
        indexes = {
            item["name"] for item in inspector.get_indexes("outreach_engagement_events")
        }
        if "ix_outreach_engagement_events_provider_key" not in indexes:
            op.create_index(
                "ix_outreach_engagement_events_provider_key",
                "outreach_engagement_events",
                ["provider_key"],
            )
        if "ix_outreach_engagement_events_provider_event_id" not in indexes:
            op.create_index(
                "ix_outreach_engagement_events_provider_event_id",
                "outreach_engagement_events",
                ["provider_event_id"],
            )

        unique_names = {
            item.get("name")
            for item in inspector.get_unique_constraints("outreach_engagement_events")
        }
        if "uq_outreach_engagement_provider_event" not in unique_names:
            op.create_unique_constraint(
                "uq_outreach_engagement_provider_event",
                "outreach_engagement_events",
                ["organization_id", "provider_key", "provider_event_id"],
            )

    if "outbound_suppressions" in tables:
        suppression_columns = {
            item["name"]: item for item in inspector.get_columns("outbound_suppressions")
        }
        if not suppression_columns.get("created_by_user_id", {}).get("nullable", True):
            op.alter_column(
                "outbound_suppressions",
                "created_by_user_id",
                existing_type=sa.String(36),
                nullable=True,
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "outreach_engagement_events" in tables:
        unique_names = {
            item.get("name")
            for item in inspector.get_unique_constraints("outreach_engagement_events")
        }
        if "uq_outreach_engagement_provider_event" in unique_names:
            op.drop_constraint(
                "uq_outreach_engagement_provider_event",
                "outreach_engagement_events",
                type_="unique",
            )
        indexes = {
            item["name"] for item in inspector.get_indexes("outreach_engagement_events")
        }
        for index_name in (
            "ix_outreach_engagement_events_provider_event_id",
            "ix_outreach_engagement_events_provider_key",
        ):
            if index_name in indexes:
                op.drop_index(index_name, table_name="outreach_engagement_events")
        columns = {item["name"] for item in inspector.get_columns("outreach_engagement_events")}
        if "provider_event_id" in columns:
            op.drop_column("outreach_engagement_events", "provider_event_id")
        if "provider_key" in columns:
            op.drop_column("outreach_engagement_events", "provider_key")
        op.alter_column(
            "outreach_engagement_events",
            "recorded_by_user_id",
            existing_type=sa.String(36),
            nullable=False,
        )

    if "outbound_suppressions" in tables:
        op.alter_column(
            "outbound_suppressions",
            "created_by_user_id",
            existing_type=sa.String(36),
            nullable=False,
        )
