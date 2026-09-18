from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_ALEMBIC_INI = _REPOSITORY_ROOT / "alembic.ini"
_MIGRATIONS_DIR = _REPOSITORY_ROOT / "migrations"


@dataclass(frozen=True)
class DatabaseSchemaStatus:
    state: str
    current_heads: tuple[str, ...]
    expected_heads: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return self.state == "current"

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.state,
            "current_heads": list(self.current_heads),
            "expected_heads": list(self.expected_heads),
        }


def expected_schema_heads() -> tuple[str, ...]:
    config = Config(str(_ALEMBIC_INI))
    config.set_main_option("script_location", str(_MIGRATIONS_DIR))
    return tuple(sorted(ScriptDirectory.from_config(config).get_heads()))


def inspect_database_schema(engine: Engine) -> DatabaseSchemaStatus:
    """Read database reachability and Alembic revision state without modifying data."""

    try:
        expected_heads = expected_schema_heads()
    except (CommandError, OSError, ValueError):
        return DatabaseSchemaStatus("migration_metadata_unavailable", (), ())

    try:
        with engine.connect() as connection:
            if connection.scalar(text("SELECT 1")) != 1:
                return DatabaseSchemaStatus("database_unavailable", (), expected_heads)
            current_heads = tuple(
                sorted(MigrationContext.configure(connection).get_current_heads())
            )
    except SQLAlchemyError:
        return DatabaseSchemaStatus("database_unavailable", (), expected_heads)

    if current_heads != expected_heads:
        return DatabaseSchemaStatus("schema_out_of_date", current_heads, expected_heads)
    return DatabaseSchemaStatus("current", current_heads, expected_heads)
