from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import hashlib

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
    if not name:
        raise ValueError("Job lock name must not be empty")
    if timeout_seconds < 0:
        raise ValueError("Job lock timeout must be zero or greater")

    database_name = engine.url.database or ""
    database_scope = hashlib.sha256(database_name.encode("utf-8")).hexdigest()[:12]
    scoped_name = f"marketingiq:{database_scope}:{name}"
    if len(scoped_name) > _MAX_MYSQL_LOCK_NAME_LENGTH:
        raise ValueError("Scoped job lock name exceeds MySQL's 64-character limit")

    with engine.connect() as connection:
        acquired = connection.scalar(
            text("SELECT GET_LOCK(:name, :timeout_seconds)"),
            {"name": scoped_name, "timeout_seconds": timeout_seconds},
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
                    {"name": scoped_name},
                )
