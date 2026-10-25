from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from test_discovery import GROUNDED, FakeDiscoveryProvider, company, seed

from marketingiq.api.app import create_app
from marketingiq.application.auth import hash_password
from marketingiq.domain.discovery import CompanyDiscoveryCandidate
from marketingiq.domain.models import (
    Base,
    MembershipRole,
    OrganizationCompany,
    OrganizationMembership,
    User,
)

SECRET = "a-secure-discovery-api-secret-that-is-long-enough"
PASSWORD = "correct horse battery staple"


def seed_api(url: str) -> dict:
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        seeded = seed(session)
        marketer = seeded["user"]
        marketer.password_hash = hash_password(PASSWORD)
        reader = User(
            email=f"discovery-reader-{uuid4().hex[:8]}@example.test",
            password_hash=hash_password(PASSWORD),
        )
        session.add(reader)
        session.flush()
        session.add_all(
            [
                OrganizationMembership(
                    organization_id=seeded["organization"].id,
                    user_id=marketer.id,
                    role=MembershipRole.MARKETING_USER,
                ),
                OrganizationMembership(
                    organization_id=seeded["organization"].id,
                    user_id=reader.id,
                    role=MembershipRole.READ_ONLY,
                ),
            ]
        )
        session.commit()
        return {
            "org_id": seeded["organization"].id,
            "product_id": seeded["product"].id,
            "marketer_email": marketer.email,
            "reader_email": reader.email,
        }


def login(client: TestClient, email: str) -> dict:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_discovery_api_run_review_and_rbac(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'discovery-api.db'}"
    seeded = seed_api(url)
    app = create_app(url, SECRET)
    app.state.discovery_provider = FakeDiscoveryProvider(
        companies=[
            company("Port Alpha", "portalpha.example", GROUNDED),
            company("Port Beta", "portbeta.example", GROUNDED),
            company("Ghost", "ghost.example", "https://never.example/"),
        ],
        retrieved=[GROUNDED],
    )
    client = TestClient(app)
    marketer = login(client, seeded["marketer_email"])
    reader = login(client, seeded["reader_email"])
    prefix = f"/api/v1/organizations/{seeded['org_id']}"
    run_url = f"{prefix}/products/{seeded['product_id']}/discovery-runs"

    status = client.get(prefix + "/discovery/status", headers=reader)
    assert status.status_code == 200
    assert status.json()["configured"] is True
    assert "api_key" not in status.text

    assert client.post(run_url, headers=reader, json={}).status_code == 403
    assert client.post(run_url, headers=marketer, json={"max_results": 26}).status_code == 422

    created = client.post(run_url, headers=marketer, json={"max_results": 5})
    assert created.status_code == 201
    run = created.json()
    assert run["status"] == "COMPLETED"
    assert (run["proposed_count"], run["skipped_count"]) == (2, 1)
    assert [c["skip_reason"] for c in run["candidates"]] == [None, None, "UNGROUNDED"]

    listed = client.get(prefix + "/discovery-candidates", headers=reader)
    assert listed.status_code == 200
    assert sorted(c["domain"] for c in listed.json()) == ["portalpha.example", "portbeta.example"]
    skipped = client.get(prefix + "/discovery-candidates?status=SKIPPED", headers=reader)
    assert [c["domain"] for c in skipped.json()] == ["ghost.example"]

    alpha = next(c for c in run["candidates"] if c["domain"] == "portalpha.example")
    beta = next(c for c in run["candidates"] if c["domain"] == "portbeta.example")
    accept_url = f"{prefix}/discovery-candidates/{alpha['id']}/accept"
    assert client.post(accept_url, headers=reader, json={}).status_code == 403
    accepted = client.post(accept_url, headers=marketer, json={"note": "Good fit"})
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "ACCEPTED"
    assert accepted.json()["organization_company_id"]
    assert client.post(accept_url, headers=marketer, json={}).status_code == 409

    rejected = client.post(
        f"{prefix}/discovery-candidates/{beta['id']}/reject", headers=marketer, json={}
    )
    assert rejected.json()["status"] == "REJECTED"

    detail = client.get(f"{prefix}/discovery-runs/{run['id']}", headers=reader)
    assert [c["status"] for c in detail.json()["candidates"]] == [
        "ACCEPTED",
        "REJECTED",
        "SKIPPED",
    ]
    runs = client.get(prefix + "/discovery-runs", headers=reader)
    assert [item["id"] for item in runs.json()] == [run["id"]]
    assert "candidates" not in runs.json()[0]

    missing = client.post(
        f"{prefix}/discovery-candidates/{uuid4()}/accept", headers=marketer, json={}
    )
    assert missing.status_code == 404

    engine = create_engine(url)
    with Session(engine) as session:
        relationship = session.get(OrganizationCompany, accepted.json()["organization_company_id"])
        assert relationship.lifecycle_status == "DISCOVERED"
        assert session.scalar(
            select(CompanyDiscoveryCandidate).where(CompanyDiscoveryCandidate.id == beta["id"])
        ).review_note is None


def test_discovery_run_conflicts_when_provider_not_configured(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'discovery-unconfigured.db'}"
    seeded = seed_api(url)
    app = create_app(url, SECRET)
    app.state.discovery_provider = FakeDiscoveryProvider(configured=False)
    client = TestClient(app)
    marketer = login(client, seeded["marketer_email"])
    response = client.post(
        f"/api/v1/organizations/{seeded['org_id']}/products/{seeded['product_id']}/discovery-runs",
        headers=marketer,
        json={},
    )
    assert response.status_code == 409
