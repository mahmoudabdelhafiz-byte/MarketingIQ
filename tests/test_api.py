from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from marketingiq.api.app import create_app
from marketingiq.application.auth import hash_password
from marketingiq.domain.models import (
    Base,
    MembershipRole,
    Organization,
    OrganizationMembership,
    User,
)


def test_api_tenant_isolation_and_shared_company(tmp_path):
    database = tmp_path / "api.db"
    url = f"sqlite+pysqlite:///{database}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        alice = User(
            email="alice@example.test", password_hash=hash_password("correct horse battery")
        )
        bob = User(email="bob@example.test", password_hash=hash_password("correct horse battery"))
        a, b = Organization(name="A", slug="a"), Organization(name="B", slug="b")
        session.add_all([alice, bob, a, b])
        session.flush()
        session.add_all(
            [
                OrganizationMembership(
                    organization_id=a.id, user_id=alice.id, role=MembershipRole.ORGANIZATION_ADMIN
                ),
                OrganizationMembership(
                    organization_id=b.id, user_id=bob.id, role=MembershipRole.ORGANIZATION_ADMIN
                ),
            ]
        )
        session.commit()
        a_id, b_id = a.id, b.id
    client = TestClient(create_app(url, "a-secure-test-secret-that-is-long-enough"))
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.test", "password": "correct horse battery"},
    )
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    product = client.post(
        f"/api/v1/organizations/{a_id}/products",
        headers=headers,
        json={"name": "Safe", "slug": "safe", "criteria": [{"kind": "country", "value": "AE"}]},
    )
    assert product.status_code == 201
    assert client.get(f"/api/v1/organizations/{b_id}/products", headers=headers).status_code == 404
    first = client.post(
        f"/api/v1/organizations/{a_id}/companies",
        headers=headers,
        json={"domain": "example.test", "canonical_name": "Example"},
    )
    assert first.status_code == 201
    alice_relationship = first.json()["id"]
    bob_login = client.post(
        "/api/v1/auth/login",
        json={"email": "bob@example.test", "password": "correct horse battery"},
    )
    bob_headers = {"Authorization": f"Bearer {bob_login.json()['access_token']}"}
    second = client.post(
        f"/api/v1/organizations/{b_id}/companies",
        headers=bob_headers,
        json={"domain": "example.test", "canonical_name": "Ignored duplicate name"},
    )
    assert second.status_code == 201
    assert second.json()["company"]["id"] == first.json()["company"]["id"]
    assert (
        client.get(
            f"/api/v1/organizations/{b_id}/companies/{alice_relationship}", headers=bob_headers
        ).status_code
        == 404
    )


def test_read_only_cannot_write(tmp_path):
    database = tmp_path / "readonly.db"
    url = f"sqlite+pysqlite:///{database}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        user = User(
            email="reader@example.test", password_hash=hash_password("correct horse battery")
        )
        org = Organization(name="Readers", slug="readers")
        session.add_all([user, org])
        session.flush()
        session.add(
            OrganizationMembership(
                organization_id=org.id, user_id=user.id, role=MembershipRole.READ_ONLY
            )
        )
        session.commit()
        org_id = org.id
    client = TestClient(create_app(url, "a-secure-test-secret-that-is-long-enough"))
    token = client.post(
        "/api/v1/auth/login", json={"email": user.email, "password": "correct horse battery"}
    ).json()["access_token"]
    response = client.post(
        f"/api/v1/organizations/{org_id}/products",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "No", "slug": "no"},
    )
    assert response.status_code == 403
