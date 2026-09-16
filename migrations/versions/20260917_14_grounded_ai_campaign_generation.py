"""Grounded AI campaign generation metadata.

Revision ID: 20260917_14
Revises: 20260917_13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260917_14"
down_revision: str | None = "20260917_13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "campaign_ai_generation_attempts" in existing:
        return
    op.create_table(
        "campaign_ai_generation_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("organization_company_id", sa.String(36), sa.ForeignKey("organization_companies.id"), nullable=False),
        sa.Column("qualification_id", sa.String(36), sa.ForeignKey("lead_qualifications.id"), nullable=False),
        sa.Column("contact_id", sa.String(36), sa.ForeignKey("contact_candidates.id"), nullable=False),
        sa.Column("draft_id", sa.String(36), sa.ForeignKey("campaign_drafts.id"), nullable=True),
        sa.Column("provider_key", sa.String(100), nullable=False),
        sa.Column("model", sa.String(150), nullable=False),
        sa.Column("prompt_version", sa.String(100), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("input_fact_keys", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("response_status", sa.String(100), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("error_category", sa.String(100), nullable=True),
        sa.Column("fallback_used", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
    )
    for column in (
        "organization_id",
        "organization_company_id",
        "qualification_id",
        "contact_id",
        "draft_id",
        "created_by_user_id",
    ):
        op.create_index(
            f"ix_campaign_ai_generation_attempts_{column}",
            "campaign_ai_generation_attempts",
            [column],
        )
    op.create_index(
        "ix_campaign_ai_attempt_tenant_time",
        "campaign_ai_generation_attempts",
        ["organization_id", "requested_at"],
    )


def downgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "campaign_ai_generation_attempts" in existing:
        op.drop_table("campaign_ai_generation_attempts")
