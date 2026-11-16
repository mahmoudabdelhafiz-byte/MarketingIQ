"""Store all text as utf8mb4 so Arabic and other non-Latin text is kept.

The production database was created with latin1 as its default, so every table inherited it and
non-Latin characters were saved as "?". This converts the database default and every table that
is not utf8mb4 yet. Latin text converts unchanged.

Revision ID: 20261121_26
Revises: 20261120_25
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261121_26"
down_revision: str | None = "20261120_25"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHARSET = "utf8mb4"
COLLATION = "utf8mb4_unicode_ci"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return
    database = bind.exec_driver_sql("SELECT DATABASE()").scalar()
    bind.exec_driver_sql(
        f"ALTER DATABASE `{database}` CHARACTER SET {CHARSET} COLLATE {COLLATION}"
    )
    tables = [
        row[0]
        for row in bind.exec_driver_sql(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE' "
            "AND (table_collation IS NULL OR table_collation NOT LIKE 'utf8mb4%%')"
        )
    ]
    if not tables:
        return
    # Foreign-key columns must share a character set; convert with checks off, then restore.
    bind.exec_driver_sql("SET FOREIGN_KEY_CHECKS = 0")
    try:
        for table in tables:
            bind.exec_driver_sql(
                f"ALTER TABLE `{table}` CONVERT TO CHARACTER SET {CHARSET} COLLATE {COLLATION}"
            )
    finally:
        bind.exec_driver_sql("SET FOREIGN_KEY_CHECKS = 1")


def downgrade() -> None:
    # Converting back to latin1 would destroy non-Latin text; intentionally a no-op.
    pass
