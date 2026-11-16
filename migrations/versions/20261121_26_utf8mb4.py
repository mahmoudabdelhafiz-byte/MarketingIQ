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

    # MariaDB will not change the character set of a column used in a foreign key, even with
    # FOREIGN_KEY_CHECKS = 0. Drop every foreign key, convert, then recreate them unchanged.
    foreign_keys = _foreign_keys(bind)
    bind.exec_driver_sql("SET FOREIGN_KEY_CHECKS = 0")
    try:
        for fk in foreign_keys:
            bind.exec_driver_sql(
                f"ALTER TABLE `{fk['table']}` DROP FOREIGN KEY `{fk['name']}`"
            )
        for table in tables:
            bind.exec_driver_sql(
                f"ALTER TABLE `{table}` CONVERT TO CHARACTER SET {CHARSET} COLLATE {COLLATION}"
            )
        for fk in foreign_keys:
            columns = ", ".join(f"`{c}`" for c in fk["columns"])
            targets = ", ".join(f"`{c}`" for c in fk["ref_columns"])
            bind.exec_driver_sql(
                f"ALTER TABLE `{fk['table']}` ADD CONSTRAINT `{fk['name']}` "
                f"FOREIGN KEY ({columns}) REFERENCES `{fk['ref_table']}` ({targets}) "
                f"ON DELETE {fk['on_delete']} ON UPDATE {fk['on_update']}"
            )
    finally:
        bind.exec_driver_sql("SET FOREIGN_KEY_CHECKS = 1")


def _foreign_keys(bind) -> list[dict]:
    rules = {
        (row[0], row[1]): (row[2], row[3], row[4])
        for row in bind.exec_driver_sql(
            "SELECT table_name, constraint_name, referenced_table_name, delete_rule, update_rule "
            "FROM information_schema.referential_constraints "
            "WHERE constraint_schema = DATABASE()"
        )
    }
    columns: dict[tuple[str, str], list[tuple[int, str, str]]] = {}
    for table, name, column, ref_column, position in bind.exec_driver_sql(
        "SELECT table_name, constraint_name, column_name, referenced_column_name, "
        "ordinal_position FROM information_schema.key_column_usage "
        "WHERE table_schema = DATABASE() AND referenced_table_name IS NOT NULL"
    ):
        columns.setdefault((table, name), []).append((position, column, ref_column))
    result = []
    for (table, name), (ref_table, on_delete, on_update) in sorted(rules.items()):
        parts = sorted(columns.get((table, name), []))
        if not parts:
            continue
        result.append(
            {
                "table": table,
                "name": name,
                "ref_table": ref_table,
                "columns": [p[1] for p in parts],
                "ref_columns": [p[2] for p in parts],
                "on_delete": on_delete,
                "on_update": on_update,
            }
        )
    return result


def downgrade() -> None:
    # Converting back to latin1 would destroy non-Latin text; intentionally a no-op.
    pass
