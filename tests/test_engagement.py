from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_campaigns import SECRET, login, seed_api_database, seed_campaign
from test_outbound import FakeSender, approved_draft, outbound_service

from marketingiq.api.app import create_app
from marketingiq.application.engagement import OutreachEngagementService
from marketingiq.application.errors import AuthorizationError, ConflictError
from marketingiq.application.outbound import OutboundSenderRegistry
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.engagement import EngagementEventType
from marketingiq.domain.models import AuditLog, MembershipRole
from marketingiq.domain.outbound import SuppressionEntry, SuppressionSource


def engagement_service(session, seeded, role=MembershipRole.MARKETING_USER):
    return OutreachEngagementService(
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
        idempotency_key="engagement-send-001",
    )
    return draft, attempt


def test_engagement_summary_and_idempotency(session):
    seeded = seed_campaign(session)
    _draft, attempt = sent_attempt(session, seeded)
    service = engagement_service(session, seeded)

    delivered = service.record(
        seeded["relationship"].id,
        attempt.id,
        event_type=EngagementEventType.DELIVERED,
        event_key="delivery-event-001",
    )
    repeated = service.record(
        seeded["relationship"].id,
        attempt.id,
        event_type=EngagementEventType.DELIVERED,
        event_key="delivery-event-001",
    )
    assert delivered.id == repeated.id

    service.record(
        seeded["relationship"].id,
        attempt.id,
        event_type=EngagementEventType.POSITIVE_REPLY,
        event_key="reply-event-00001",
    )
    summary = service.summary(seeded["relationship"].id, attempt.id)
    assert summary["delivery_status"] == "DELIVERED"
    assert summary["replied"] is True
    assert summary["reply_sentiment"] == "POSITIVE"
    assert summary["event_count"] == 2

    with pytest.raises(ConflictError, match="ENGAGEMENT_EVENT_KEY_REUSED"):
        service.record(
            seeded["relationship"].id,
            attempt.id,
            event_type=EngagementEventType.BOUNCED,
            event_key="delivery-event-001",
        )


def test_bounce_and_opt_out_create_suppression_without_email_in_audit(session):
    seeded = seed_campaign(session)
    _draft, attempt = sent_attempt(session, seeded)
    service = engagement_service(session, seeded)

    service.record(
        seeded["relationship"].id,
        attempt.id,
        event_type=EngagementEventType.BOUNCED,
        event_key="bounce-event-0001",
        reason_code="MAILBOX_UNAVAILABLE",
    )
    suppression = session.scalar(select(SuppressionEntry))
    assert suppression is not None
    assert suppression.source == SuppressionSource.BOUNCE
    assert suppression.email_normalized == seeded["email"].email

    audit = session.scalar(
        select(AuditLog).where(AuditLog.action == "outreach_engagement.recorded")
    )
    assert audit is not None
    assert seeded["email"].email not in str(audit.metadata_json)


def test_read_only_can_view_but_cannot_record(session):
    seeded = seed_campaign(session)
    _draft, attempt = sent_attempt(session, seeded)
    marketing = engagement_service(session, seeded)
    marketing.record(
        seeded["relationship"].id,
        attempt.id,
        event_type=EngagementEventType.REPLIED,
        event_key="reader-view-0001",
    )

    reader = engagement_service(session, seeded, MembershipRole.READ_ONLY)
    assert len(reader.list(seeded["relationship"].id, attempt.id)) == 1
    with pytest.raises(AuthorizationError):
        reader.record(
            seeded["relationship"].id,
            attempt.id,
            event_type=EngagementEventType.OPT_OUT,
            event_key="reader-denied-001",
        )


def test_engagement_api_records_and_summarizes(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'engagement-api.db'}"
    seeded = seed_api_database(url)
    app = create_app(url, SECRET)
    fake = FakeSender()
    app.state.outbound_sender_registry = OutboundSenderRegistry([fake])
    client = TestClient(app)
    marketing = login(client, seeded["marketing_email"])
    reader = login(client, seeded["reader_email"])

    base = (
        f"/api/v1/organizations/{seeded['org_id']}/companies/"
        f"{seeded['relationship_id']}/campaign-drafts"
    )
    generated = client.post(
        base,
        headers=marketing,
        json={
            "qualification_id": seeded["qualification_id"],
            "contact_id": seeded["contact_id"],
        },
    )
    draft_id = generated.json()["id"]
    assert client.post(
        f"{base}/{draft_id}/approve",
        headers=marketing,
        json={"reason": "Reviewed"},
    ).status_code == 200
    sent = client.post(
        f"{base}/{draft_id}/send",
        headers=marketing,
        json={"provider": "SMTP", "idempotency_key": "eng-api-send-001"},
    )
    assert sent.status_code == 201
    attempt_id = sent.json()["id"]
    engagement_url = f"{base}/{draft_id}/send-attempts/{attempt_id}/engagements"

    denied = client.post(
        engagement_url,
        headers=reader,
        json={"event_type": "DELIVERED", "event_key": "eng-api-event-01"},
    )
    assert denied.status_code == 403

    recorded = client.post(
        engagement_url,
        headers=marketing,
        json={"event_type": "DELIVERED", "event_key": "eng-api-event-01"},
    )
    assert recorded.status_code == 201
    assert recorded.json()["event_type"] == "DELIVERED"

    summary = client.get(engagement_url + "/summary", headers=reader)
    assert summary.status_code == 200
    assert summary.json()["delivery_status"] == "DELIVERED"
    assert summary.json()["event_count"] == 1
