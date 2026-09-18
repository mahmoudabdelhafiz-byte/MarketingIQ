from __future__ import annotations

import os
import subprocess
import sys


def _run_alembic_current(database_url: str | None):
    env = os.environ.copy()
    if database_url is None:
        env.pop("DATABASE_URL", None)
    else:
        env["DATABASE_URL"] = database_url
    return subprocess.run(
        [sys.executable, "-m", "alembic", "current"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_alembic_refuses_missing_database_url():
    result = _run_alembic_current(None)

    assert result.returncode != 0
    assert "DATABASE_URL must be set for Alembic migrations" in result.stderr
    assert "postgresql+psycopg" not in result.stderr


def test_alembic_refuses_non_mysql_database_url():
    result = _run_alembic_current("sqlite+pysqlite:///:memory:")

    assert result.returncode != 0
    assert "Alembic migrations require a mysql+pymysql DATABASE_URL" in result.stderr


def test_alembic_refuses_malformed_database_url():
    result = _run_alembic_current("not a valid database url")

    assert result.returncode != 0
    assert "DATABASE_URL is invalid for Alembic migrations" in result.stderr
