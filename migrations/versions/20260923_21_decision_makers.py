"""Decision-maker discovery: product personas, contact evidence and contact qualification columns.

Revision ID: 20260923_21
Revises: 20260923_20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260923_21"
down_revision: str | None = "20260923_20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PERSONAS = "product_personas"
EVIDENCE = "contact_evidence"
CONTACTS = "contact_candidates"
EMAILS = "contact_emails"

CONTACT_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    ("persona_id", sa.String(36)),
    ("persona_name", sa.String(120)),
    ("position_raw", sa.String(255)),
    ("linkedin_url", sa.String(500)),
    ("decision_maker", sa.Boolean()),
    ("role_match", sa.String(20)),
    ("role_confidence", sa.Integer()),
    ("role_reason", sa.String(300)),
    ("role_resolver", sa.String(20)),
    ("employment_status", sa.String(30)),
    ("employment_checked_at", sa.DateTime(timezone=True)),
    ("suitability", sa.String(30)),
    ("suitability_components", sa.JSON()),
    ("contact_status", sa.String(40)),
    ("persona_rank", sa.Integer()),
    ("review_status", sa.String(20)),
    ("review_note", sa.String(500)),
    ("reviewed_by_user_id", sa.String(36)),
    ("reviewed_at", sa.DateTime(timezone=True)),
    ("last_discovery_at", sa.DateTime(timezone=True)),
)
EMAIL_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    ("origin", sa.String(30)),
    ("verification_source", sa.String(50)),
)


def _columns(inspector, table: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table)}


def _indexes(inspector, table: str) -> set[str]:
    return {index["name"] for index in inspector.get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if PERSONAS not in tables:
        op.create_table(
            PERSONAS,
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("organization_id", sa.String(36), nullable=False),
            sa.Column("product_id", sa.String(36), nullable=False),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("titles", sa.JSON(), nullable=True),
            sa.Column("departments", sa.JSON(), nullable=True),
            sa.Column("seniorities", sa.JSON(), nullable=True),
            sa.Column("max_contacts", sa.Integer(), nullable=False),
            sa.Column("priority", sa.Integer(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
            sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("product_id", "name", name="uq_product_persona_name"),
        )
        op.create_index(
            "ix_product_persona_tenant_product", PERSONAS, ["organization_id", "product_id"]
        )

    if EVIDENCE not in tables:
        op.create_table(
            EVIDENCE,
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("organization_id", sa.String(36), nullable=False),
            sa.Column("contact_id", sa.String(36), nullable=False),
            sa.Column("source_type", sa.String(40), nullable=False),
            sa.Column("provider_key", sa.String(100), nullable=True),
            sa.Column("provider_reference", sa.String(255), nullable=True),
            sa.Column("source_url", sa.String(2048), nullable=True),
            sa.Column("claimed_name", sa.String(220), nullable=True),
            sa.Column("claimed_title", sa.String(255), nullable=True),
            sa.Column("claimed_company", sa.String(255), nullable=True),
            sa.Column("snippet", sa.String(500), nullable=True),
            sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_seen_on", sa.DateTime(timezone=True), nullable=True),
            sa.Column("still_on_page", sa.Boolean(), nullable=True),
            sa.Column("confidence", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
            sa.ForeignKeyConstraint(["contact_id"], [f"{CONTACTS}.id"], ondelete="CASCADE"),
        )
        op.create_index("ix_contact_evidence_contact", EVIDENCE, ["contact_id", "retrieved_at"])
        op.create_index("ix_contact_evidence_organization_id", EVIDENCE, ["organization_id"])

    existing = _columns(inspector, CONTACTS)
    for name, column_type in CONTACT_COLUMNS:
        if name not in existing:
            op.add_column(CONTACTS, sa.Column(name, column_type, nullable=True))
    if "ix_contact_candidates_persona_id" not in _indexes(sa.inspect(bind), CONTACTS):
        op.create_index("ix_contact_candidates_persona_id", CONTACTS, ["persona_id"])

    existing = _columns(inspector, EMAILS)
    for name, column_type in EMAIL_COLUMNS:
        if name not in existing:
            op.add_column(EMAILS, sa.Column(name, column_type, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    existing = _columns(inspector, EMAILS)
    for name, _ in reversed(EMAIL_COLUMNS):
        if name in existing:
            op.drop_column(EMAILS, name)

    if "ix_contact_candidates_persona_id" in _indexes(inspector, CONTACTS):
        op.drop_index("ix_contact_candidates_persona_id", table_name=CONTACTS)
    existing = _columns(inspector, CONTACTS)
    for name, _ in reversed(CONTACT_COLUMNS):
        if name in existing:
            op.drop_column(CONTACTS, name)

    if EVIDENCE in tables:
        op.drop_table(EVIDENCE)
    if PERSONAS in tables:
        op.drop_table(PERSONAS)
