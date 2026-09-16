from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_campaigns import SECRET, login, seed_api_database, seed_campaign
from test_engagement import sent_attempt
from test_outbound import FakeSender

from marketingiq.api.app import create_app
from marketingiq.application.engagement import ProviderEngagementIngestionService
from marketingiq.application.errors import ConflictError, NotFoundError
from marketingiq.application.outbound import OutboundSenderRegistry
from marketingiq.domain.engagement import EngagementEventType, EngagementSource, OutreachEngagementEvent
from marketingiq.domain.models import AuditLog
from marketingiq.domain.outbound import SuppressionEntry, SuppressionSource

WEBHOOK_SECRET = "marketingiq-webhook-test-secret-000001"


def test_provider_ingestion_is_idempotent_and_system_attributed(session):
    seeded = seed_campaign(session)
    _draft, attempt = sent_attempt(session, seeded)
    service = ProviderEngagementIngestionService(
        session,
        now=datetime(2026, 9, 17, 2, 15, tzinfo=UTC),
    )

    event = service.ingest(
        seeded["organization"].id,
        "smtp",
        attempt.id,
        provider_message_id=attempt.provider_message_id,
        provider_event_id="provider-bounce-event-001",
        event_type=EngagementEventType.BOUNCED,
        occurred_at=datetime(2026, 9, 17, 2, 14, tzinfo=UTC),
        reason_code="MAILBOX_UNAVAILABLE",
    )
    repeated = service.ingest(
        seeded["organization"].id,
        "SMTP",
        attempt.id,
        provider_message_id=attempt.provider_message_id,
        provider_event_id="provider-bounce-event-001",
        event_type=EngagementEventType.BOUNCED,
        occurred_at=datetime(2026, 9, 17, 2, 14, tzinfo=UTC),
    )

    assert repeated.id == event.id
    assert event.source == EngagementSource.PROVIDER
    assert event.provider_key == "SMTP"
    assert event.provider_event_id == "provider-bounce-event-001"
    assert event.recorded_by_user_id is None

    suppression = session.scalar(select(SuppressionEntry))
    assert suppression is not None
    assert suppression.source == SuppressionSource.BOUNCE
    assert suppression.created_by_user_id is None

    audit = session.scalar(
        select(AuditLog).where(AuditLog.action == "outreach_engagement.provider_ingested")
    )
    assert audit is not None
    assert audit.actor_user_id is None
    assert seeded["email"].email not in str(audit.metadata_json)


def test_provider_ingestion_rejects_untrusted_claims_and_mismatched_attempt(session):
    seeded = seed_campaign(session)
    _draft, attempt = sent_attempt(session, seeded)
    service = ProviderEngagementIngestionService(session)

    with pytest.raises(ConflictError, match="ENGAGEMENT_PROVIDER_EVENT_TYPE_UNSUPPORTED"):
        service.ingest(
            seeded["organization"].id,
            "SMTP",
            attempt.id,
            provider_message_id=attempt.provider_message_id,
            provider_event_id="provider-positive-001",
            event_type=EngagementEventType.POSITIVE_REPLY,
            occurred_at=datetime.now(UTC),
        )

    with pytest.raises(NotFoundError, match="Outbound send attempt not found"):
        service.ingest(
            seeded["organization"].id,
            "SMTP",
            attempt.id,
            provider_message_id="wrong-provider-message-id",
            provider_event_id="provider-delivery-001",
            event_type=EngagementEventType.DELIVERED,
            occurred_at=datetime.now(UTC),
        )


def test_signed_webhook_ingests_without_tenant_jwt_and_rejects_bad_signatures(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ENGAGEMENT_WEBHOOK_SECRET", WEBHOOK_SECRET)
    url = f"sqlite+pysqlite:///{tmp_path / 'engagement-webhook.db'}"
    seeded = seed_api_database(url)
    app = create_app(url, SECRET)
    app.state.outbound_sender_registry = OutboundSenderRegistry([FakeSender()])
    client = TestClient(app)
    marketing = login(client, seeded["marketing_email"])

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
    assert generated.status_code == 201
    draft_id = generated.json()["id"]
    assert client.post(
        f"{base}/{draft_id}/approve",
        headers=marketing,
        json={"reason": "Reviewed"},
    ).status_code == 200
    sent = client.post(
        f"{base}/{draft_id}/send",
        headers=marketing,
        json={"provider": "SMTP", "idempotency_key": "webhook-send-001"},
    )
    assert sent.status_code == 201
    attempt = sent.json()

    payload = {
        "send_attempt_id": attempt["id"],
        "provider_message_id": attempt["provider_message_id"],
        "provider_event_id": "provider-delivery-api-001",
        "event_type": "DELIVERED",
        "occurred_at": datetime.now(UTC).isoformat(),
        "reason_code": None,
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    timestamp = str(int(time.time()))
    signature = _signature(raw, timestamp)
    webhook_url = (
        f"/api/v1/organizations/{seeded['org_id']}"
        "/webhooks/outbound/SMTP/engagement"
    )

    denied = client.post(
        webhook_url,
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-MarketingIQ-Webhook-Timestamp": timestamp,
            "X-MarketingIQ-Webhook-Signature": "sha256=bad-signature",
        },
    )
    assert denied.status_code == 401

    accepted = client.post(
        webhook_url,
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-MarketingIQ-Webhook-Timestamp": timestamp,
            "X-MarketingIQ-Webhook-Signature": f"sha256={signature}",
        },
    )
    assert accepted.status_code == 200
    assert accepted.json()["source"] == "PROVIDER"
    assert accepted.json()["provider_key"] == "SMTP"
    assert accepted.json()["recorded_by_user_id"] is None

    repeated = client.post(
        webhook_url,
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-MarketingIQ-Webhook-Timestamp": timestamp,
            "X-MarketingIQ-Webhook-Signature": signature,
        },
    )
    assert repeated.status_code == 200
    assert repeated.json()["id"] == accepted.json()["id"]

    with app.state.outbound_sender_registry._senders["SMTP"] if False else pytest.raises(Exception):
        pass


def test_webhook_rejects_expired_signature_before_processing(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGAGEMENT_WEBHOOK_SECRET", WEBHOOK_SECRET)
    url = f"sqlite+pysqlite:///{tmp_path / 'expired-webhook.db'}"
    seeded = seed_api_database(url)
    client = TestClient(create_app(url, SECRET))
    raw = b"{}"
    timestamp = "1"
    response = client.post(
        f"/api/v1/organizations/{seeded['org_id']}/webhooks/outbound/SMTP/engagement",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-MarketingIQ-Webhook-Timestamp": timestamp,
            "X-MarketingIQ-Webhook-Signature": _signature(raw, timestamp),
        },
    )
    assert response.status_code == 401
    assert "timestamp" in response.json()["detail"].lower()


def _signature(raw: bytes, timestamp: str) -> str:
    return hmac.new(
        WEBHOOK_SECRET.encode("utf-8"),
        timestamp.encode("ascii") + b"." + raw,
        hashlib.sha256,
    ).hexdigest()
