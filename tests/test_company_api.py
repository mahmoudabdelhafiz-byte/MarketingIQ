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

SECRET = "a-secure-test-secret-that-is-long-enough"
PASSWORD = "correct horse battery"


def test_company_routes_use_jwt_membership_and_import_permissions(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'companies.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        organization = Organization(name="API Tenant", slug="api-tenant")
        admin = User(email="admin-api@example.test", password_hash=hash_password(PASSWORD))
        reader = User(email="reader-api@example.test", password_hash=hash_password(PASSWORD))
        session.add_all([organization, admin, reader])
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
        session.commit()
        organization_id = organization.id

    client = TestClient(create_app(url, SECRET))

    def headers(email):
        response = client.post(
            "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
        )
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    path = f"/api/v1/organizations/{organization_id}/companies"
    reader_headers = headers("reader-api@example.test")
    assert client.post(
        path,
        headers=reader_headers,
        json={"domain": "api.test", "canonical_name": "API"},
    ).status_code == 403
    assert client.post(
        f"/api/v1/organizations/{organization_id}/company-imports/preview",
        headers=reader_headers,
        json={"content": "company_name,domain\nAPI,api.test\n"},
    ).status_code == 403

    admin_headers = headers("admin-api@example.test")
    created = client.post(
        path,
        headers=admin_headers,
        json={
            "domain": "https://www.api.test/",
            "canonical_name": "API",
            "private_notes": "tenant secret",
        },
    )
    assert created.status_code == 201
    assert created.json()["domain"] == "api.test"
    listing = client.get(path, headers=reader_headers)
    assert listing.status_code == 200
    assert listing.json()[0]["private_notes"] == "tenant secret"
