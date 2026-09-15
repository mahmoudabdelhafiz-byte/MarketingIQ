import importlib

import pytest

from marketingiq.domain.models import (
    MembershipRole,
    Organization,
    OrganizationMembership,
    User,
)

pytest.importorskip("fastapi")
TestClient = importlib.import_module("fastapi.testclient").TestClient
api_module = importlib.import_module("marketingiq.api.app")
app = api_module.app
get_session = api_module.get_session


def test_internal_company_routes_enforce_membership_and_roles(session):
    organization = Organization(name="API Tenant", slug="api-tenant")
    admin = User(email="admin-api@example.test", password_hash="hash")
    outsider = User(email="outsider-api@example.test", password_hash="hash")
    reader = User(email="reader-api@example.test", password_hash="hash")
    session.add_all([organization, admin, outsider, reader])
    session.flush()
    session.add_all(
        [
            OrganizationMembership(
                organization_id=organization.id,
                user_id=admin.id,
                role=MembershipRole.ORGANIZATION_ADMIN,
            ),
            OrganizationMembership(
                organization_id=organization.id,
                user_id=reader.id,
                role=MembershipRole.READ_ONLY,
            ),
        ]
    )
    session.flush()

    def override_session():
        yield session

    app.dependency_overrides[get_session] = override_session
    client = TestClient(app)
    path = f"/api/v1/organizations/{organization.id}/companies"
    try:
        denied = client.get(path, headers={"X-User-ID": outsider.id})
        assert denied.status_code == 403
        read_only_write = client.post(
            path,
            headers={"X-User-ID": reader.id},
            json={"domain": "api.test", "canonical_name": "API"},
        )
        assert read_only_write.status_code == 403
        created = client.post(
            path,
            headers={"X-User-ID": admin.id},
            json={
                "domain": "https://www.api.test/",
                "canonical_name": "API",
                "private_notes": "tenant secret",
            },
        )
        assert created.status_code == 201
        assert created.json()["domain"] == "api.test"
        listing = client.get(path, headers={"X-User-ID": reader.id})
        assert listing.status_code == 200
        assert listing.json()[0]["private_notes"] == "tenant secret"
    finally:
        app.dependency_overrides.clear()
