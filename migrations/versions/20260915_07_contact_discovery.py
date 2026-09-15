"""Tenant-private contact discovery and business email channels.

Revision ID: 20260915_07
Revises: 20260915_06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260915_07"
down_revision: str | None = "20260915_06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if "contact_candidates" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "contact_candidates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False
        ),
        sa.Column(
            "organization_company_id",
            sa.String(36),
            sa.ForeignKey("organization_companies.id"),
            nullable=False,
        ),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column(
            "qualification_id",
            sa.String(36),
            sa.ForeignKey("lead_qualifications.id"),
            nullable=False,
        ),
        sa.Column("provider_key", sa.String(100), nullable=False),
        sa.Column("provider_contact_reference", sa.String(255)),
        sa.Column("first_name", sa.String(100)),
        sa.Column("last_name", sa.String(100)),
        sa.Column("full_name", sa.String(220)),
        sa.Column("job_title", sa.String(200)),
        sa.Column("department", sa.String(100)),
        sa.Column("seniority", sa.String(100)),
        sa.Column("normalized_buyer_role", sa.String(200), nullable=False),
        sa.Column("buyer_role_match", sa.String(20), nullable=False),
        sa.Column("buyer_role_match_reason", sa.String(255), nullable=False),
        sa.Column("confidence", sa.Integer()),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True)),
        sa.Column("classification", sa.String(30), nullable=False),
        sa.Column("redistribution_status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "provider_key", "provider_contact_reference"),
    )
    op.create_index(
        "ix_contact_tenant_relationship",
        "contact_candidates",
        ["organization_id", "organization_company_id"],
    )
    for col in ("organization_id", "company_id", "qualification_id"):
        op.create_index(f"ix_contact_candidates_{col}", "contact_candidates", [col])
    op.create_table(
        "contact_emails",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "contact_id",
            sa.String(36),
            sa.ForeignKey("contact_candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("email_type", sa.String(30), nullable=False),
        sa.Column("source_provider", sa.String(100), nullable=False),
        sa.Column("verification_status", sa.String(20), nullable=False),
        sa.Column("verification_score", sa.Integer()),
        sa.Column("found_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("classification", sa.String(30), nullable=False),
        sa.Column("redistribution_status", sa.String(20), nullable=False),
        sa.UniqueConstraint("contact_id", "email"),
    )
    op.create_index("ix_contact_emails_contact_id", "contact_emails", ["contact_id"])


def downgrade() -> None:
    op.drop_table("contact_emails")
    op.drop_table("contact_candidates")
