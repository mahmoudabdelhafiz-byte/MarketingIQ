"""People imported from the organization's own lists.

Revision ID: 20261120_25
Revises: 20261119_24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261120_25"
down_revision: str | None = "20261119_24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "imported_contacts"


def upgrade() -> None:
    if TABLE in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        TABLE,
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
        sa.Column("dedupe_key", sa.String(320), nullable=False),
        sa.Column("list_name", sa.String(120), nullable=False),
        sa.Column("first_name", sa.String(100), nullable=True),
        sa.Column("last_name", sa.String(100), nullable=True),
        sa.Column("full_name", sa.String(220), nullable=True),
        sa.Column("job_title", sa.String(200), nullable=True),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("linkedin_url", sa.String(500), nullable=True),
        sa.Column("trusted", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column(
            "contact_id", sa.String(36), sa.ForeignKey("contact_candidates.id"), nullable=True
        ),
        sa.Column("created_by_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attached_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("organization_id", "dedupe_key", name="uq_imported_contact_key"),
        sa.CheckConstraint(
            "status IN ('PENDING', 'ATTACHED', 'SUPPRESSED')", name="imported_contact_status"
        ),
    )
    for column in ("organization_id", "organization_company_id", "company_id"):
        op.create_index(f"ix_{TABLE}_{column}", TABLE, [column])
    op.create_index(
        "ix_imported_contact_rel_status", TABLE, ["organization_company_id", "status"]
    )


def downgrade() -> None:
    if TABLE in set(sa.inspect(op.get_bind()).get_table_names()):
        op.drop_table(TABLE)
