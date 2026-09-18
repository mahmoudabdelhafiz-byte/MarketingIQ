from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_campaigns import SECRET, login, seed_api_database, seed_campaign
from test_engagement import engagement_service
from test_outbound import FakeSender, approved_draft, outbound_service

from marketingiq.api.app import create_app
from marketingiq.application.errors import AuthorizationError, ConflictError
from marketingiq.application.outbound import OutboundSenderRegistry
from marketingiq.application.pipeline import SalesPipelineService
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.engagement import EngagementEventType
from marketingiq.domain.models import AuditLog, MembershipRole
from marketingiq.domain.pipeline import OpportunityStage, SalesOpportunityStageEvent


def pipeline_service(session, seeded, role=MembershipRole.MARKETING_USER):
    return SalesPipelineService(
        session,
        TenantContext(seeded["organization"].id, seeded["user"].id, role),
    )


def sent_attempt(session, seeded):
    draft, _ = approved_draft(session, seeded)
    sender = FakeSender()
    attempt = outbound_service(session, seeded, sender).send(
        seeded["relationship"].id,
        draft.id,
        provider_key="SMTP",
        idempotency_key="pipeline-send-001",
    )
    return draft, attempt


def test_create_opportunity_is_idempotent_and_tracks_initial_stage(session):
    seeded = seed_campaign(session)
    _draft, attempt = sent_attempt(session, seeded)
    service = pipeline_service(session, seeded)

    opportunity = service.create(
        seeded["relationship"].id,
        attempt.id,
        estimated_value=12000,
        currency="usd",
        next_action="Book discovery call",
    )
    repeated = service.create(seeded["relationship"].id, attempt.id)

    assert repeated.id == opportunity.id
    assert opportunity.stage == OpportunityStage.CONTACTED
    assert opportunity.currency == "USD"
    history = service.history(seeded["relationship"].id, opportunity.id)
    assert len(history) == 1
    assert history[0].from_stage is None
    assert history[0].to_stage == OpportunityStage.CONTACTED


def test_reply_before_creation_starts_at_responded(session):
    seeded = seed_campaign(session)
    _draft, attempt = sent_attempt(session, seeded)
    engagement_service(session, seeded).record(
        seeded["relationship"].id,
        attempt.id,
        event_type=EngagementEventType.POSITIVE_REPLY,
        event_key="pipeline-positive-reply-001",
    )

    opportunity = pipeline_service(session, seeded).create(
        seeded["relationship"].id,
        attempt.id,
    )
    assert opportunity.stage == OpportunityStage.RESPONDED


def test_forward_stage_history_terminal_rules_and_safe_audit(session):
    seeded = seed_campaign(session)
    _draft, attempt = sent_attempt(session, seeded)
    service = pipeline_service(session, seeded)
    opportunity = service.create(seeded["relationship"].id, attempt.id)

    service.move_stage(
        seeded["relationship"].id,
        opportunity.id,
        OpportunityStage.RESPONDED,
        note="Customer replied and requested a call",
    )
    service.move_stage(
        seeded["relationship"].id,
        opportunity.id,
        OpportunityStage.MEETING,
    )
    service.move_stage(
        seeded["relationship"].id,
        opportunity.id,
        OpportunityStage.PROPOSAL,
    )
    service.move_stage(
        seeded["relationship"].id,
        opportunity.id,
        OpportunityStage.WON,
        reason_code="CUSTOMER_ACCEPTED",
    )

    history = service.history(seeded["relationship"].id, opportunity.id)
    assert [event.to_stage for event in history] == [
        OpportunityStage.CONTACTED,
        OpportunityStage.RESPONDED,
        OpportunityStage.MEETING,
        OpportunityStage.PROPOSAL,
        OpportunityStage.WON,
    ]
    assert session.scalar(select(SalesOpportunityStageEvent)) is not None

    with pytest.raises(ConflictError, match="OPPORTUNITY_STAGE_IS_TERMINAL"):
        service.move_stage(
            seeded["relationship"].id,
            opportunity.id,
            OpportunityStage.LOST,
        )

    audits = list(
        session.scalars(
            select(AuditLog).where(AuditLog.entity_type == "sales_opportunity")
        )
    )
    assert audits
    audit_text = str([item.metadata_json for item in audits])
    assert "Customer replied and requested a call" not in audit_text
    assert seeded["email"].email not in audit_text


def test_cannot_move_backward_and_won_requires_proposal(session):
    seeded = seed_campaign(session)
    _draft, attempt = sent_attempt(session, seeded)
    service = pipeline_service(session, seeded)
    opportunity = service.create(seeded["relationship"].id, attempt.id)

    with pytest.raises(ConflictError, match="OPPORTUNITY_WON_REQUIRES_PROPOSAL"):
        service.move_stage(
            seeded["relationship"].id,
            opportunity.id,
            OpportunityStage.WON,
        )

    service.move_stage(
        seeded["relationship"].id,
        opportunity.id,
        OpportunityStage.MEETING,
    )
    with pytest.raises(ConflictError, match="OPPORTUNITY_STAGE_CANNOT_MOVE_BACKWARD"):
        service.move_stage(
            seeded["relationship"].id,
            opportunity.id,
            OpportunityStage.RESPONDED,
        )


def test_read_only_can_view_pipeline_but_cannot_modify(session):
    seeded = seed_campaign(session)
    _draft, attempt = sent_attempt(session, seeded)
    marketing = pipeline_service(session, seeded)
    opportunity = marketing.create(seeded["relationship"].id, attempt.id)

    reader = pipeline_service(session, seeded, MembershipRole.READ_ONLY)
    assert reader.get(seeded["relationship"].id, opportunity.id).id == opportunity.id
    assert len(reader.history(seeded["relationship"].id, opportunity.id)) == 1
    with pytest.raises(AuthorizationError):
        reader.move_stage(
            seeded["relationship"].id,
            opportunity.id,
            OpportunityStage.RESPONDED,
        )


def test_pipeline_api_create_stage_history_and_rbac(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'pipeline-api.db'}"
    seeded = seed_api_database(url)
    app = create_app(url, SECRET)
    fake = FakeSender()
    app.state.outbound_sender_registry = OutboundSenderRegistry([fake])
    client = TestClient(app)
    marketing = login(client, seeded["marketing_email"])
    reader = login(client, seeded["reader_email"])

    campaign_base = (
        f"/api/v1/organizations/{seeded['org_id']}/companies/"
        f"{seeded['relationship_id']}/campaign-drafts"
    )
    generated = client.post(
        campaign_base,
        headers=marketing,
        json={
            "qualification_id": seeded["qualification_id"],
            "contact_id": seeded["contact_id"],
        },
    )
    assert generated.status_code == 201
    draft_id = generated.json()["id"]
    assert client.post(
        f"{campaign_base}/{draft_id}/approve",
        headers=marketing,
        json={"reason": "Reviewed"},
    ).status_code == 200
    sent = client.post(
        f"{campaign_base}/{draft_id}/send",
        headers=marketing,
        json={"provider": "SMTP", "idempotency_key": "pipeline-api-send-001"},
    )
    assert sent.status_code == 201

    opportunity_base = (
        f"/api/v1/organizations/{seeded['org_id']}/companies/"
        f"{seeded['relationship_id']}/opportunities"
    )
    denied = client.post(
        opportunity_base,
        headers=reader,
        json={"send_attempt_id": sent.json()["id"]},
    )
    assert denied.status_code == 403

    created = client.post(
        opportunity_base,
        headers=marketing,
        json={
            "send_attempt_id": sent.json()["id"],
            "estimated_value": 5000,
            "currency": "SAR",
        },
    )
    assert created.status_code == 201
    assert created.json()["stage"] == "CONTACTED"
    opportunity_id = created.json()["id"]

    moved = client.post(
        f"{opportunity_base}/{opportunity_id}/stage",
        headers=marketing,
        json={"stage": "RESPONDED", "reason_code": "REPLY_RECEIVED"},
    )
    assert moved.status_code == 200
    assert moved.json()["stage"] == "RESPONDED"

    history = client.get(
        f"{opportunity_base}/{opportunity_id}/history",
        headers=reader,
    )
    assert history.status_code == 200
    assert [item["to_stage"] for item in history.json()] == [
        "CONTACTED",
        "RESPONDED",
    ]
