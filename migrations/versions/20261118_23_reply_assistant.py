"""Reply assistant: captured prospect replies, product knowledge base and booking link.

Revision ID: 20261118_23
Revises: 20261117_22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261118_23"
down_revision: str | None = "20261117_22"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "inbound_replies"
INTENTS = (
    "MEETING_REQUEST",
    "INTERESTED",
    "QUESTION",
    "NOT_NOW",
    "NOT_INTERESTED",
    "UNSUBSCRIBE",
    "OUT_OF_OFFICE",
    "OTHER",
)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    product_columns = {column["name"] for column in inspector.get_columns("products")}
    if "knowledge_base" not in product_columns:
        op.add_column("products", sa.Column("knowledge_base", sa.Text(), nullable=True))
    if "booking_url" not in product_columns:
        op.add_column("products", sa.Column("booking_url", sa.String(500), nullable=True))

    if TABLE in set(inspector.get_table_names()):
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
        sa.Column(
            "initial_attempt_id",
            sa.String(36),
            sa.ForeignKey("outbound_send_attempts.id"),
            nullable=False,
        ),
        sa.Column("draft_id", sa.String(36), sa.ForeignKey("campaign_drafts.id"), nullable=False),
        sa.Column(
            "contact_id", sa.String(36), sa.ForeignKey("contact_candidates.id"), nullable=False
        ),
        sa.Column(
            "contact_email_id", sa.String(36), sa.ForeignKey("contact_emails.id"), nullable=False
        ),
        sa.Column("message_key", sa.String(150), nullable=False),
        sa.Column("message_id", sa.String(255), nullable=True),
        sa.Column("from_email", sa.String(320), nullable=True),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(9), nullable=False),
        sa.Column("intent", sa.String(15), nullable=False),
        sa.Column("intent_source", sa.String(20), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("summary", sa.String(500), nullable=True),
        sa.Column("language", sa.String(10), nullable=False),
        sa.Column("suggested_subject", sa.String(255), nullable=True),
        sa.Column("suggested_body", sa.Text(), nullable=True),
        sa.Column("suggested_slots", sa.JSON(), nullable=False),
        sa.Column("suggestion_model", sa.String(100), nullable=True),
        sa.Column("answer_subject", sa.String(255), nullable=True),
        sa.Column("answer_body", sa.Text(), nullable=True),
        sa.Column("answer_message_id", sa.String(255), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("handled_by_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("outcome", sa.String(40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "message_key", name="uq_inbound_reply_message"),
        sa.CheckConstraint(
            _in("status", ("NEW", "ANSWERED", "DISMISSED")), name="inbound_reply_status"
        ),
        sa.CheckConstraint(_in("intent", INTENTS), name="inbound_reply_intent"),
    )
    for column in (
        "organization_id",
        "organization_company_id",
        "company_id",
        "initial_attempt_id",
        "draft_id",
        "contact_id",
        "answer_message_id",
    ):
        op.create_index(f"ix_{TABLE}_{column}", TABLE, [column])
    op.create_index(
        "ix_inbound_reply_org_status", TABLE, ["organization_id", "status", "received_at"]
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if TABLE in set(inspector.get_table_names()):
        op.drop_table(TABLE)
    product_columns = {column["name"] for column in inspector.get_columns("products")}
    for column in ("booking_url", "knowledge_base"):
        if column in product_columns:
            op.drop_column("products", column)
