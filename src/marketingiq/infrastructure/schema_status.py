from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

_PROJECT_ROOT_ENV = "MARKETINGIQ_PROJECT_ROOT"
_SOURCE_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _resolve_migration_paths() -> tuple[Path, Path]:
    candidates: list[Path] = []
    configured_root = os.environ.get(_PROJECT_ROOT_ENV, "").strip()
    if configured_root:
        candidates.append(Path(configured_root).expanduser())

    candidates.extend((Path.cwd(), _SOURCE_REPOSITORY_ROOT))

    seen: set[Path] = set()
    for candidate in candidates:
        root = candidate.resolve()
        if root in seen:
            continue
        seen.add(root)

        alembic_ini = root / "alembic.ini"
        migrations_dir = root / "migrations"
        if alembic_ini.is_file() and migrations_dir.is_dir():
            return alembic_ini, migrations_dir

    raise FileNotFoundError(
        "Could not locate alembic.ini and migrations/. "
        f"Set {_PROJECT_ROOT_ENV} to the MarketingIQ application root."
    )


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
    alembic_ini, migrations_dir = _resolve_migration_paths()
    config = Config(str(alembic_ini))
    config.set_main_option("script_location", str(migrations_dir))
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
