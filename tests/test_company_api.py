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


def seed_user(session, org, email, role):
    user = User(email=email, password_hash=hash_password("correct horse battery"))
    session.add(user)
    session.flush()
    session.add(
        OrganizationMembership(organization_id=org.id, user_id=user.id, role=role)
    )
    return user


def login(client, email):
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "correct horse battery"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_authenticated_company_fact_and_import_api(tmp_path):
    database = tmp_path / "company-api.db"
    url = f"sqlite+pysqlite:///{database}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        org = Organization(name="Barmageyat", slug="barmageyat-api")
        session.add(org)
        session.flush()
        seed_user(session, org, "admin@example.test", MembershipRole.ORGANIZATION_ADMIN)
        seed_user(session, org, "marketer@example.test", MembershipRole.MARKETING_USER)
        session.commit()
        org_id = org.id

    client = TestClient(create_app(url, "a-secure-test-secret-that-is-long-enough"))
    admin_headers = login(client, "admin@example.test")
    marketer_headers = login(client, "marketer@example.test")

    company = client.post(
        f"/api/v1/organizations/{org_id}/companies",
        headers=marketer_headers,
        json={
            "domain": "https://www.example.com/",
            "canonical_name": "Example",
            "country_code": "SA",
            "industry": "Technology",
        },
    )
    assert company.status_code == 201
    assert company.json()["domain"] == "example.com"
    relationship_id = company.json()["id"]

    source = client.post(
        f"/api/v1/organizations/{org_id}/data-sources",
        headers=marketer_headers,
        json={"provider_key": "MANUAL", "display_name": "Manual"},
    )
    assert source.status_code == 201

    fact = client.post(
        f"/api/v1/organizations/{org_id}/companies/{relationship_id}/facts",
        headers=marketer_headers,
        json={
            "fact_key": "industry",
            "value": "Technology",
            "classification": "CUSTOMER_PROVIDED",
            "confidence": 90,
            "evidence": [
                {"data_source_id": source.json()["id"], "reference_text": "Customer file"}
            ],
        },
    )
    assert fact.status_code == 201
    assert fact.json()["redistribution_status"] == "UNKNOWN"

    csv_content = "company_name,domain,country_code\nABC Logistics,abc-logistics.test,SA\n"
    denied = client.post(
        f"/api/v1/organizations/{org_id}/company-imports/preview",
        headers=marketer_headers,
        json={"content": csv_content},
    )
    assert denied.status_code == 403

    preview = client.post(
        f"/api/v1/organizations/{org_id}/company-imports/preview",
        headers=admin_headers,
        json={"content": csv_content},
    )
    assert preview.status_code == 200
    assert preview.json()["new_companies"] == 1

    committed = client.post(
        f"/api/v1/organizations/{org_id}/company-imports",
        headers=admin_headers,
        json={"content": csv_content},
    )
    assert committed.status_code == 201
    assert committed.json()["valid_rows"] == 1
