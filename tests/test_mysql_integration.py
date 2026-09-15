import os

import pytest
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

pytestmark = pytest.mark.mysql
SECRET = "a-secure-test-secret-that-is-long-enough"
PASSWORD = "correct horse battery"


@pytest.fixture
def mysql_api():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    engine = create_engine(url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        org_a, org_b = Organization(name="A", slug="a"), Organization(name="B", slug="b")
        admin = User(email="admin@example.test", password_hash=hash_password(PASSWORD))
        marketer = User(email="marketer@example.test", password_hash=hash_password(PASSWORD))
        reader = User(email="reader@example.test", password_hash=hash_password(PASSWORD))
        other = User(email="other@example.test", password_hash=hash_password(PASSWORD))
        super_admin = User(
            email="super@example.test", password_hash=hash_password(PASSWORD), is_super_admin=True
        )
        session.add_all([org_a, org_b, admin, marketer, reader, other, super_admin])
        session.flush()
        session.add_all(
            [
                OrganizationMembership(
                    organization_id=org_a.id,
                    user_id=admin.id,
                    role=MembershipRole.ORGANIZATION_ADMIN,
                ),
                OrganizationMembership(
                    organization_id=org_a.id,
                    user_id=marketer.id,
                    role=MembershipRole.MARKETING_USER,
                ),
                OrganizationMembership(
                    organization_id=org_a.id, user_id=reader.id, role=MembershipRole.READ_ONLY
                ),
                OrganizationMembership(
                    organization_id=org_b.id,
                    user_id=other.id,
                    role=MembershipRole.ORGANIZATION_ADMIN,
                ),
            ]
        )
        session.commit()
        ids = org_a.id, org_b.id
    yield TestClient(create_app(url, SECRET)), ids
    Base.metadata.drop_all(engine)
    engine.dispose()


def headers(client, email):
    response = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_mysql_tenant_isolation_and_role_matrix(mysql_api):
    client, (org_a, org_b) = mysql_api
    admin, marketer = (
        headers(client, "admin@example.test"),
        headers(client, "marketer@example.test"),
    )
    reader, other = headers(client, "reader@example.test"), headers(client, "other@example.test")
    super_admin = headers(client, "super@example.test")

    product_a = client.post(
        f"/api/v1/organizations/{org_a}/products",
        headers=admin,
        json={"name": "Admin Product", "slug": "admin-product"},
    )
    product_b = client.post(
        f"/api/v1/organizations/{org_b}/products",
        headers=other,
        json={"name": "Other Product", "slug": "other-product"},
    )
    assert product_a.status_code == product_b.status_code == 201
    assert (
        client.post(
            f"/api/v1/organizations/{org_a}/products",
            headers=marketer,
            json={"name": "Marketing Product", "slug": "marketing-product"},
        ).status_code
        == 201
    )
    assert (
        client.post(
            f"/api/v1/organizations/{org_a}/products",
            headers=reader,
            json={"name": "Forbidden", "slug": "forbidden"},
        ).status_code
        == 403
    )
    assert (
        client.get(
            f"/api/v1/organizations/{org_b}/products/{product_b.json()['id']}", headers=admin
        ).status_code
        == 404
    )
    assert (
        client.get(f"/api/v1/organizations/{org_a}/products", headers=super_admin).status_code
        == 404
    )

    icp_b = client.post(
        f"/api/v1/organizations/{org_b}/products/{product_b.json()['id']}/icps",
        headers=other,
        json={"name": "Other ICP"},
    )
    assert icp_b.status_code == 201
    assert (
        client.put(
            f"/api/v1/organizations/{org_a}/icps/{icp_b.json()['id']}",
            headers=admin,
            json={"name": "Stolen"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/organizations/{org_a}/products/{product_b.json()['id']}/icps",
            headers=admin,
            json={"name": "Cross tenant"},
        ).status_code
        == 404
    )

    company_a = client.post(
        f"/api/v1/organizations/{org_a}/companies",
        headers=admin,
        json={"domain": "shared.example", "canonical_name": "Shared"},
    )
    company_b = client.post(
        f"/api/v1/organizations/{org_b}/companies",
        headers=other,
        json={"domain": "shared.example", "canonical_name": "Not overwritten"},
    )
    assert company_a.status_code == company_b.status_code == 201
    assert company_a.json()["company"]["id"] == company_b.json()["company"]["id"]
    relation_a = company_a.json()["id"]
    assert (
        client.patch(
            f"/api/v1/organizations/{org_a}/companies/{relation_a}",
            headers=admin,
            json={"private_notes": "A only", "lifecycle_status": "QUALIFIED"},
        ).status_code
        == 200
    )
    assert (
        client.get(
            f"/api/v1/organizations/{org_b}/companies/{relation_a}", headers=other
        ).status_code
        == 404
    )
    b_private = client.get(
        f"/api/v1/organizations/{org_b}/companies/{company_b.json()['id']}", headers=other
    )
    assert b_private.status_code == 200
    assert b_private.json()["private_notes"] is None
