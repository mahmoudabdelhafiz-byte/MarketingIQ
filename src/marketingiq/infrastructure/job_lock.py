from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, text

_MAX_MYSQL_LOCK_NAME_LENGTH = 64


@contextmanager
def mysql_job_lock(
    engine: Engine,
    name: str,
    *,
    timeout_seconds: int = 0,
) -> Iterator[bool]:
    """Hold a MySQL user-level advisory lock for one scheduled job invocation."""

    if engine.dialect.name != "mysql":
        raise RuntimeError("Scheduled job locks require MySQL")
    if not name or len(name) > _MAX_MYSQL_LOCK_NAME_LENGTH:
        raise ValueError("Job lock name must contain between 1 and 64 characters")
    if timeout_seconds < 0:
        raise ValueError("Job lock timeout must be zero or greater")

    with engine.connect() as connection:
        acquired = connection.scalar(
            text("SELECT GET_LOCK(:name, :timeout_seconds)"),
            {"name": name, "timeout_seconds": timeout_seconds},
        )
        if acquired is None:
            raise RuntimeError("Unable to evaluate scheduled job lock")
        locked = int(acquired) == 1
        try:
            yield locked
        finally:
            if locked:
                connection.execute(
                    text("SELECT RELEASE_LOCK(:name)"),
                    {"name": name},
                )
