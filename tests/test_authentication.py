from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from marketingiq.api.app import create_app
from marketingiq.application.auth import TOKEN_AUDIENCE, TOKEN_ISSUER, hash_password
from marketingiq.domain.models import (
    Base,
    MembershipRole,
    Organization,
    OrganizationMembership,
    User,
)

SECRET = "a-secure-test-secret-that-is-long-enough"
PASSWORD = "correct horse battery"


@pytest.fixture
def auth_api(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'auth.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        active = User(email="active@example.test", password_hash=hash_password(PASSWORD))
        inactive = User(
            email="inactive@example.test", password_hash=hash_password(PASSWORD), is_active=False
        )
        outsider = User(
            email="outsider@example.test",
            password_hash=hash_password(PASSWORD),
            is_super_admin=True,
        )
        org = Organization(name="Tenant", slug="tenant")
        session.add_all([active, inactive, outsider, org])
        session.flush()
        session.add(
            OrganizationMembership(
                organization_id=org.id, user_id=active.id, role=MembershipRole.MARKETING_USER
            )
        )
        session.commit()
        values = active.id, org.id
    return TestClient(create_app(url, SECRET)), values


def login(client, email="active@example.test", password=PASSWORD):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def test_login_accepts_correct_password_and_rejects_bad_or_inactive(auth_api):
    client, _ = auth_api
    assert login(client).status_code == 200
    assert login(client, password="incorrect password").status_code == 401
    assert login(client, email="inactive@example.test").status_code == 401


def test_tokens_and_unauthenticated_access_are_rejected(auth_api):
    client, (user_id, org_id) = auth_api
    assert client.get(f"/api/v1/organizations/{org_id}/products").status_code == 401
    malformed = {"Authorization": "Bearer not-a-token"}
    assert client.get("/api/v1/me", headers=malformed).status_code == 401
    now = datetime.now(UTC)
    expired = jwt.encode(
        {
            "sub": user_id,
            "iat": now - timedelta(hours=2),
            "exp": now - timedelta(hours=1),
            "iss": TOKEN_ISSUER,
            "aud": TOKEN_AUDIENCE,
        },
        SECRET,
        algorithm="HS256",
    )
    assert (
        client.get("/api/v1/me", headers={"Authorization": f"Bearer {expired}"}).status_code == 401
    )


def test_membership_and_conflicting_context_are_rejected(auth_api):
    client, (_, org_id) = auth_api
    outsider_token = login(client, email="outsider@example.test").json()["access_token"]
    headers = {"Authorization": f"Bearer {outsider_token}"}
    assert (
        client.get(f"/api/v1/organizations/{org_id}/products", headers=headers).status_code == 404
    )
    active_headers = {
        "Authorization": f"Bearer {login(client).json()['access_token']}",
        "X-Organization-ID": "another-organization",
    }
    assert (
        client.get(f"/api/v1/organizations/{org_id}/products", headers=active_headers).status_code
        == 400
    )


def test_auth_secret_is_required_and_has_minimum_length(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTH_SECRET", raising=False)
    url = f"sqlite+pysqlite:///{tmp_path / 'secret.db'}"
    with pytest.raises(RuntimeError, match="AUTH_SECRET"):
        create_app(url)
    with pytest.raises(RuntimeError, match="32 characters"):
        create_app(url, "too-short")
