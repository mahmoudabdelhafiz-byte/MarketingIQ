"""Database-backed failed-login throttling.

Counters live in the database rather than process memory so that every Passenger worker on
shared hosting enforces the same limits. Keys are SHA-256 digests of the normalized email or
client address, so the table never stores raw emails or IP addresses.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from marketingiq.domain.models import LoginThrottle


class LoginThrottled(Exception):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("Too many failed sign-in attempts")
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class LoginThrottlePolicy:
    window_seconds: int = 900
    max_failures_per_email: int = 8
    max_failures_per_address: int = 40
    retention_seconds: int = 86400


def _digest(kind: str, value: str) -> str:
    return f"{kind}:" + hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()


class LoginThrottleService:
    def __init__(
        self,
        session: Session,
        policy: LoginThrottlePolicy | None = None,
        now: datetime | None = None,
    ) -> None:
        self.session = session
        self.policy = policy or LoginThrottlePolicy()
        self.now = now or datetime.now(UTC)

    def _window_start(self) -> datetime:
        epoch = int(self.now.timestamp())
        start = epoch - epoch % self.policy.window_seconds
        return datetime.fromtimestamp(start, UTC)

    def _limits(self, email: str, address: str | None) -> list[tuple[str, int]]:
        limits = [(_digest("email", email), self.policy.max_failures_per_email)]
        if address:
            limits.append((_digest("addr", address), self.policy.max_failures_per_address))
        return limits

    def check(self, email: str, address: str | None) -> None:
        window = self._window_start()
        for key, limit in self._limits(email, address):
            count = self.session.scalar(
                select(LoginThrottle.failure_count).where(
                    LoginThrottle.bucket_key == key,
                    LoginThrottle.window_start == window,
                )
            )
            if count is not None and count >= limit:
                elapsed = int(self.now.timestamp()) - int(window.timestamp())
                raise LoginThrottled(max(1, self.policy.window_seconds - elapsed))

    def record_failure(self, email: str, address: str | None) -> None:
        window = self._window_start()
        for key, _limit in self._limits(email, address):
            row = self.session.scalar(
                select(LoginThrottle).where(
                    LoginThrottle.bucket_key == key,
                    LoginThrottle.window_start == window,
                )
            )
            if row is not None:
                row.failure_count += 1
                row.updated_at = self.now
                continue
            row = LoginThrottle(
                bucket_key=key,
                window_start=window,
                failure_count=1,
                updated_at=self.now,
            )
            if self.session.get_bind().dialect.name == "sqlite":
                # SQLite serializes writers, and pysqlite savepoints are unreliable.
                self.session.add(row)
                self.session.flush()
                continue
            try:
                with self.session.begin_nested():
                    self.session.add(row)
            except IntegrityError:
                # Another worker created the same window concurrently; count against it.
                existing = self.session.scalar(
                    select(LoginThrottle).where(
                        LoginThrottle.bucket_key == key,
                        LoginThrottle.window_start == window,
                    )
                )
                if existing is not None:
                    existing.failure_count += 1
        cutoff = self.now - timedelta(seconds=self.policy.retention_seconds)
        self.session.execute(
            delete(LoginThrottle)
            .where(LoginThrottle.window_start < cutoff)
            .execution_options(synchronize_session=False)
        )
        self.session.flush()
