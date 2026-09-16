from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from test_campaigns import SECRET, login, seed_api_database, seed_campaign

from marketingiq.api.app import create_app
from marketingiq.application.errors import ConflictError
from marketingiq.application.orchestration import (
    AutomationOrchestrationService,
    OrchestrationStep,
)
from marketingiq.application.research import ProviderRegistry
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.models import ContactCandidate, ContactEmail, MembershipRole, Product


def service(session, seeded, role=MembershipRole.MARKETING_USER):
    return AutomationOrchestrationService(
        session,
        TenantContext(seeded["organization"].id, seeded["user"].id, role),
        ProviderRegistry([]),
    )


def test_plan_stops_before_human_campaign_work(session):
    seeded = seed_campaign(session)

    plan = service(session, seeded).plan(
        seeded["relationship"].id,
        seeded["product"].id,
    )

    assert plan["state"] == "READY_FOR_HUMAN_CAMPAIGN_REVIEW"
    assert plan["next_step"] is None
    assert plan["contact_count"] == 1
    assert plan["human_gate"] == {
        "campaign_generation": True,
        "campaign_approval": True,
        "outbound_send": True,
    }
    assert [item["status"] for item in plan["steps"]] == [
        "OPTIONAL",
        "DONE",
        "DONE",
        "DONE",
    ]


def test_contact_discovery_requires_explicit_provider_credit_approval(session):
    seeded = seed_campaign(session)
    session.delete(seeded["email"])
    session.delete(seeded["contact"])
    session.flush()
    orchestration = service(session, seeded)

    plan = orchestration.plan(seeded["relationship"].id, seeded["product"].id)
    assert plan["next_step"] == "DISCOVER_CONTACTS"
    assert plan["steps"][-1]["requires_provider_credits"] is True

    with pytest.raises(ConflictError, match="PROVIDER_CREDIT_APPROVAL_REQUIRED"):
        orchestration.execute_step(
            seeded["relationship"].id,
            seeded["product"].id,
            OrchestrationStep.DISCOVER_CONTACTS,
        )

    assert session.scalar(select(ContactCandidate)) is None
    assert session.scalar(select(ContactEmail)) is None


def test_guarded_run_stops_at_provider_credit_gate_without_spending(session):
    seeded = seed_campaign(session)
    session.delete(seeded["email"])
    session.delete(seeded["contact"])
    session.flush()

    result = service(session, seeded).run_until_gate(
        seeded["relationship"].id,
        seeded["product"].id,
    )

    assert result["executed_steps"] == []
    assert result["stop_reason"] == "PROVIDER_CREDIT_APPROVAL_REQUIRED"
    assert result["plan"]["next_step"] == "DISCOVER_CONTACTS"
    assert session.scalar(select(ContactCandidate)) is None
    assert session.scalar(select(ContactEmail)) is None


def test_guarded_run_stops_at_human_campaign_gate(session):
    seeded = seed_campaign(session)

    result = service(session, seeded).run_until_gate(
        seeded["relationship"].id,
        seeded["product"].id,
    )

    assert result["executed_steps"] == []
    assert result["stop_reason"] == "HUMAN_CAMPAIGN_GATE"
    assert result["plan"]["state"] == "READY_FOR_HUMAN_CAMPAIGN_REVIEW"


def test_plan_is_readable_but_execution_keeps_existing_rbac(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'orchestration-api.db'}"
    seeded = seed_api_database(url)
    engine = create_engine(url)
    with Session(engine) as session:
        product_id = session.scalar(select(Product.id))
    assert product_id is not None

    client = TestClient(create_app(url, SECRET))
    reader = login(client, seeded["reader_email"])
    base = (
        f"/api/v1/organizations/{seeded['org_id']}/companies/"
        f"{seeded['relationship_id']}/automation"
    )

    plan = client.get(base + "/plan", headers=reader, params={"product_id": product_id})
    assert plan.status_code == 200
    assert plan.json()["state"] == "READY_FOR_HUMAN_CAMPAIGN_REVIEW"

    denied = client.post(
        base + "/execute",
        headers=reader,
        params={"product_id": product_id},
        json={"step": "RESEARCH_PUBLIC"},
    )
    assert denied.status_code == 403

    denied_run = client.post(
        base + "/run",
        headers=reader,
        params={"product_id": product_id},
        json={},
    )
    assert denied_run.status_code == 403
