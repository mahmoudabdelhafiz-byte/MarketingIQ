"""Frozen Sprint 1 baseline schema.

Revision ID: 20260914_01
Revises: None

This revision is intentionally self-contained. It represents the schema immediately before
20260915_02 was introduced and must never import current application model metadata.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260914_01"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


membership_role = sa.Enum(
    "ORGANIZATION_ADMIN",
    "MARKETING_USER",
    "READ_ONLY",
    name="membershiprole",
    native_enum=False,
    create_constraint=True,
)
product_status = sa.Enum(
    "DRAFT",
    "ACTIVE",
    "ARCHIVED",
    name="productstatus",
    native_enum=False,
    create_constraint=True,
)
research_status = sa.Enum(
    "PENDING",
    "RUNNING",
    "SUCCEEDED",
    "FAILED",
    name="researchstatus",
    native_enum=False,
    create_constraint=True,
)
data_classification = sa.Enum(
    "PUBLIC_EVIDENCE",
    "THIRD_PARTY_LICENSED",
    "CUSTOMER_PROVIDED",
    "MARKETINGIQ_DERIVED",
    name="dataclassification",
    native_enum=False,
    create_constraint=True,
)
redistribution_status = sa.Enum(
    "UNKNOWN",
    "ALLOWED",
    "RESTRICTED",
    "INTERNAL_ONLY",
    name="redistributionstatus",
    native_enum=False,
    create_constraint=True,
)


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False, unique=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("is_super_admin", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "products",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("organization_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("value_proposition", sa.Text(), nullable=True),
        sa.Column("employee_min", sa.Integer(), nullable=True),
        sa.Column("employee_max", sa.Integer(), nullable=True),
        sa.Column("status", product_status, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "slug"),
        sa.UniqueConstraint("id", "organization_id"),
    )
    op.create_index("ix_products_organization_id", "products", ["organization_id"])
    op.create_table(
        "organization_memberships",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("organization_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("role", membership_role, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "user_id"),
    )
    op.create_table(
        "product_criteria",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("product_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("value", sa.String(500), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("product_id", "kind", "value"),
    )
    op.create_table(
        "icps",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("organization_id", sa.String(36), nullable=False),
        sa.Column("product_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("employee_min", sa.Integer(), nullable=True),
        sa.Column("employee_max", sa.Integer(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(
            ["product_id", "organization_id"],
            ["products.id", "products.organization_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("product_id", "name", "version"),
    )
    op.create_index("ix_icps_organization_id", "icps", ["organization_id"])
    op.create_table(
        "icp_criteria",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("icp_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("value", sa.String(500), nullable=False),
        sa.ForeignKeyConstraint(["icp_id"], ["icps.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("icp_id", "kind", "value"),
    )
    op.create_table(
        "companies",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("canonical_name", sa.String(255), nullable=False),
        sa.Column("website_url", sa.String(2048), nullable=True),
        sa.Column("country_code", sa.String(2), nullable=True),
        sa.Column("industry", sa.String(200), nullable=True),
        sa.Column("employee_min", sa.Integer(), nullable=True),
        sa.Column("employee_max", sa.Integer(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_researched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_companies_canonical_name", "companies", ["canonical_name"])
    op.create_table(
        "company_identifiers",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("company_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(50), nullable=False),
        sa.Column("value", sa.String(500), nullable=False),
        sa.Column("normalized_value", sa.String(500), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", "normalized_value"),
    )
    op.create_table(
        "organization_companies",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("organization_id", sa.String(36), nullable=False),
        sa.Column("company_id", sa.String(36), nullable=False),
        sa.Column("lifecycle_status", sa.String(50), nullable=True),
        sa.Column("private_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "company_id"),
    )
    op.create_index(
        "ix_organization_companies_organization_id",
        "organization_companies",
        ["organization_id"],
    )
    op.create_table(
        "data_sources",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("provider_key", sa.String(100), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("external_reference", sa.String(500), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_key", "external_reference"),
    )
    op.create_table(
        "research_runs",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("organization_id", sa.String(36), nullable=True),
        sa.Column("company_id", sa.String(36), nullable=False),
        sa.Column("initiated_by_user_id", sa.String(36), nullable=True),
        sa.Column("purpose", sa.String(100), nullable=False),
        sa.Column("status", research_status, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["initiated_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_research_runs_organization_id", "research_runs", ["organization_id"])
    op.create_index("ix_research_runs_company_id", "research_runs", ["company_id"])
    op.create_table(
        "company_facts",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("company_id", sa.String(36), nullable=False),
        sa.Column("organization_id", sa.String(36), nullable=True),
        sa.Column("fact_key", sa.String(150), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("classification", data_classification, nullable=False),
        sa.Column("redistribution_status", redistribution_status, nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("model_version", sa.String(100), nullable=True),
        sa.Column("research_run_id", sa.String(36), nullable=True),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 100", name="ck_fact_confidence"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["research_run_id"], ["research_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_company_facts_company_id", "company_facts", ["company_id"])
    op.create_index("ix_company_facts_organization_id", "company_facts", ["organization_id"])
    op.create_index(
        "ix_fact_company_key_observed",
        "company_facts",
        ["company_id", "fact_key", "observed_at"],
    )
    op.create_table(
        "evidence",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("company_fact_id", sa.String(36), nullable=False),
        sa.Column("data_source_id", sa.String(36), nullable=True),
        sa.Column("reference_url", sa.String(2048), nullable=True),
        sa.Column("reference_text", sa.Text(), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["company_fact_id"], ["company_facts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["data_source_id"], ["data_sources.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "human_overrides",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("organization_id", sa.String(36), nullable=False),
        sa.Column("company_fact_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("replacement_value", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["company_fact_id"], ["company_facts.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_human_overrides_organization_id", "human_overrides", ["organization_id"])
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("organization_id", sa.String(36), nullable=True),
        sa.Column("actor_user_id", sa.String(36), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(100), nullable=False),
        sa.Column("entity_id", sa.String(36), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_tenant_time",
        "audit_logs",
        ["organization_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("human_overrides")
    op.drop_table("evidence")
    op.drop_table("company_facts")
    op.drop_table("research_runs")
    op.drop_table("data_sources")
    op.drop_table("organization_companies")
    op.drop_table("company_identifiers")
    op.drop_table("companies")
    op.drop_table("icp_criteria")
    op.drop_table("icps")
    op.drop_table("product_criteria")
    op.drop_table("organization_memberships")
    op.drop_table("products")
    op.drop_table("users")
    op.drop_table("organizations")
