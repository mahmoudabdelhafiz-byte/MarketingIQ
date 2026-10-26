from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from marketingiq.api.app import create_app
from marketingiq.application import auth
from marketingiq.application.auth import hash_password
from marketingiq.application.login_throttle import (
    LoginThrottled,
    LoginThrottlePolicy,
    LoginThrottleService,
)
from marketingiq.domain.models import Base, LoginThrottle, User

SECRET = "a-secure-test-secret-that-is-long-enough"
PASSWORD = "correct horse battery"
NOW = datetime(2026, 9, 23, 10, 5, tzinfo=UTC)


def test_throttle_blocks_after_limit_and_resets_next_window(session):
    policy = LoginThrottlePolicy(window_seconds=900, max_failures_per_email=3)
    service = LoginThrottleService(session, policy, now=NOW)
    for _ in range(3):
        service.check("Owner@Example.com", "203.0.113.5")
        service.record_failure("owner@example.com", "203.0.113.5")

    with pytest.raises(LoginThrottled) as blocked:
        service.check("OWNER@example.com ", "198.51.100.7")
    assert 0 < blocked.value.retry_after_seconds <= 900

    later = LoginThrottleService(session, policy, now=NOW + timedelta(minutes=15))
    later.check("owner@example.com", "203.0.113.5")


def test_throttle_limits_by_client_address_across_emails(session):
    policy = LoginThrottlePolicy(max_failures_per_email=100, max_failures_per_address=2)
    service = LoginThrottleService(session, policy, now=NOW)
    service.record_failure("a@example.com", "203.0.113.9")
    service.record_failure("b@example.com", "203.0.113.9")

    with pytest.raises(LoginThrottled):
        service.check("c@example.com", "203.0.113.9")
    service.check("c@example.com", "198.51.100.1")


def test_throttle_stores_only_digests_and_prunes_old_windows(session):
    old = LoginThrottleService(session, now=NOW - timedelta(days=2))
    old.record_failure("owner@example.com", "203.0.113.5")
    LoginThrottleService(session, now=NOW).record_failure("owner@example.com", "203.0.113.5")

    rows = session.scalars(select(LoginThrottle)).all()
    assert len(rows) == 2
    assert all("owner@example.com" not in row.bucket_key for row in rows)
    assert all("203.0.113.5" not in row.bucket_key for row in rows)


def test_login_api_returns_429_after_repeated_failures(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'throttle.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(User(email="owner@example.test", password_hash=hash_password(PASSWORD)))
        session.commit()
    client = TestClient(create_app(url, SECRET))

    def attempt(password):
        return client.post(
            "/api/v1/auth/login",
            json={"email": "owner@example.test", "password": password},
        )

    statuses = [attempt("wrong password!").status_code for _ in range(8)]
    assert statuses == [401] * 8
    blocked = attempt(PASSWORD)
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(LoginThrottle)) == 2


def test_unknown_and_inactive_accounts_still_run_password_verification(session, monkeypatch):
    session.add(
        User(email="inactive@example.test", password_hash=hash_password(PASSWORD), is_active=False)
    )
    session.commit()
    calls = []
    original = auth.password_hash.verify

    def counting_verify(password, stored):
        calls.append(stored)
        return original(password, stored)

    monkeypatch.setattr(auth.password_hash, "verify", counting_verify)

    assert auth.authenticate(session, "missing@example.test", PASSWORD) is None
    assert auth.authenticate(session, "inactive@example.test", PASSWORD) is None
    assert len(calls) == 2
