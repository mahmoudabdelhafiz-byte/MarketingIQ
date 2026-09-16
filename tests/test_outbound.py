from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from test_campaigns import NOW, SECRET, login, seed_api_database, seed_campaign, service_for

from marketingiq.api.app import create_app
from marketingiq.application.errors import AuthorizationError, ConflictError
from marketingiq.application.outbound import OutboundSenderRegistry, OutboundSendService
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.models import (
    AuditLog,
    CompanyFact,
    DataClassification,
    MembershipRole,
    RedistributionStatus,
)
from marketingiq.domain.outbound import (
    OutboundProviderError,
    OutboundSendAttempt,
    OutboundSendResult,
    OutboundSendStatus,
    SuppressionSource,
)


class FakeSender:
    key = "SMTP"

    def __init__(self, *, configured=True, error: OutboundProviderError | None = None):
        self._configured = configured
        self.error = error
        self.calls = []

    @property
    def configured(self):
        return self._configured

    def send(self, message):
        self.calls.append(message)
        if self.error:
            raise self.error
        return OutboundSendResult("SMTP", True, "<fake-message-id@example.test>")


def outbound_service(session, seeded, sender, role=MembershipRole.MARKETING_USER):
    return OutboundSendService(
        session,
        TenantContext(seeded["organization"].id, seeded["user"].id, role),
        OutboundSenderRegistry([sender]),
        NOW,
    )


def approved_draft(session, seeded):
    campaign = service_for(session, seeded)
    draft = campaign.generate(
        seeded["relationship"].id,
        seeded["qualification"].id,
        seeded["contact"].id,
    )
    approval = campaign.approve(seeded["relationship"].id, draft.id, "Human approved")
    return draft, approval


def test_send_requires_approval_and_is_idempotent(session):
    seeded = seed_campaign(session)
    sender = FakeSender()
    outbound = outbound_service(session, seeded, sender)
    campaign = service_for(session, seeded)
    draft = campaign.generate(
        seeded["relationship"].id,
        seeded["qualification"].id,
        seeded["contact"].id,
    )

    with pytest.raises(ConflictError, match="OUTBOUND_DRAFT_NOT_APPROVED"):
        outbound.send(
            seeded["relationship"].id,
            draft.id,
            provider_key="SMTP",
            idempotency_key="send-key-0001",
        )
    assert sender.calls == []

    approval = campaign.approve(seeded["relationship"].id, draft.id)
    first = outbound.send(
        seeded["relationship"].id,
        draft.id,
        provider_key="SMTP",
        idempotency_key="send-key-0001",
    )
    repeated = outbound.send(
        seeded["relationship"].id,
        draft.id,
        provider_key="SMTP",
        idempotency_key="send-key-0001",
    )

    assert first.id == repeated.id
    assert first.status == OutboundSendStatus.SENT
    assert first.review_event_id == approval.id
    assert first.provider_message_id == "<fake-message-id@example.test>"
    assert len(sender.calls) == 1
    assert sender.calls[0].recipient == seeded["email"].email

    with pytest.raises(ConflictError, match="OUTBOUND_DRAFT_ALREADY_SENT"):
        outbound.send(
            seeded["relationship"].id,
            draft.id,
            provider_key="SMTP",
            idempotency_key="send-key-0002",
        )

    audit = session.scalar(select(AuditLog).where(AuditLog.action == "outbound_send.sent"))
    metadata = str(audit.metadata_json)
    assert seeded["email"].email not in metadata
    assert "Human approved" not in metadata
    assert "focused B2B" not in metadata


def test_suppression_blocks_provider_call_and_is_tenant_private(session):
    seeded = seed_campaign(session)
    sender = FakeSender()
    outbound = outbound_service(session, seeded, sender)
    draft, _ = approved_draft(session, seeded)

    suppression = outbound.suppress(
        seeded["email"].email.upper(),
        reason="Recipient requested no outreach",
        source=SuppressionSource.OPT_OUT,
    )
    assert suppression.email_normalized == seeded["email"].email
    assert outbound.suppress(seeded["email"].email).id == suppression.id

    with pytest.raises(ConflictError, match="OUTBOUND_RECIPIENT_SUPPRESSED"):
        outbound.send(
            seeded["relationship"].id,
            draft.id,
            provider_key="SMTP",
            idempotency_key="suppressed-001",
        )
    assert sender.calls == []

    reader = outbound_service(session, seeded, sender, MembershipRole.READ_ONLY)
    assert reader.list_suppressions()[0].id == suppression.id
    with pytest.raises(AuthorizationError):
        reader.suppress("another@example.test")
    with pytest.raises(AuthorizationError):
        reader.send(
            seeded["relationship"].id,
            draft.id,
            provider_key="SMTP",
            idempotency_key="reader-key-001",
        )

    audit = session.scalar(
        select(AuditLog).where(AuditLog.action == "outbound_suppression.created")
    )
    assert seeded["email"].email not in str(audit.metadata_json)


def test_provider_failure_and_not_configured_are_persisted_as_safe_attempts(session):
    seeded = seed_campaign(session)
    draft, _ = approved_draft(session, seeded)

    sender = FakeSender(error=OutboundProviderError("secret smtp detail"))
    outbound = outbound_service(session, seeded, sender)
    failed = outbound.send(
        seeded["relationship"].id,
        draft.id,
        provider_key="SMTP",
        idempotency_key="failed-key-001",
    )
    assert failed.status == OutboundSendStatus.FAILED
    assert failed.error_category == "PROVIDER_ERROR"
    assert len(sender.calls) == 1
    assert session.get(OutboundSendAttempt, failed.id).status == OutboundSendStatus.FAILED
    failed_audit = session.scalar(
        select(AuditLog).where(AuditLog.action == "outbound_send.failed")
    )
    assert "secret smtp detail" not in str(failed_audit.metadata_json)

    other = seed_campaign(session)
    other_draft, _ = approved_draft(session, other)
    unconfigured = FakeSender(configured=False)
    other_outbound = outbound_service(session, other, unconfigured)
    attempt = other_outbound.send(
        other["relationship"].id,
        other_draft.id,
        provider_key="SMTP",
        idempotency_key="not-config-001",
    )
    assert attempt.status == OutboundSendStatus.FAILED
    assert attempt.error_category == "NOT_CONFIGURED"
    assert unconfigured.calls == []


def test_send_rechecks_freshness_after_approval(session):
    seeded = seed_campaign(session)
    draft, _ = approved_draft(session, seeded)
    sender = FakeSender()
    outbound = outbound_service(session, seeded, sender)

    session.add(
        CompanyFact(
            company_id=seeded["company"].id,
            fact_key="industry",
            value="Financial Services",
            classification=DataClassification.PUBLIC_EVIDENCE,
            redistribution_status=RedistributionStatus.ALLOWED,
            confidence=99,
            observed_at=NOW + timedelta(minutes=1),
        )
    )
    session.flush()

    with pytest.raises(ConflictError, match="OUTBOUND_REQUALIFICATION_REQUIRED"):
        outbound.send(
            seeded["relationship"].id,
            draft.id,
            provider_key="SMTP",
            idempotency_key="stale-key-001",
        )
    assert sender.calls == []


def test_outbound_api_requires_human_approval_and_respects_rbac(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'outbound-api.db'}"
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
    assert generated.status_code == 201
    draft_id = generated.json()["id"]
    send_url = f"{base}/{draft_id}/send"
    payload = {"provider": "SMTP", "idempotency_key": "api-send-key-001"}

    unapproved = client.post(send_url, headers=marketing, json=payload)
    assert unapproved.status_code == 409
    assert fake.calls == []

    approved = client.post(
        f"{base}/{draft_id}/approve",
        headers=marketing,
        json={"reason": "Reviewed"},
    )
    assert approved.status_code == 200

    denied = client.post(send_url, headers=reader, json=payload)
    assert denied.status_code == 403
    sent = client.post(send_url, headers=marketing, json=payload)
    assert sent.status_code == 201
    assert sent.json()["status"] == "SENT"
    assert len(fake.calls) == 1

    repeated = client.post(send_url, headers=marketing, json=payload)
    assert repeated.status_code == 201
    assert repeated.json()["id"] == sent.json()["id"]
    assert len(fake.calls) == 1

    attempts = client.get(f"{base}/{draft_id}/send-attempts", headers=reader)
    assert attempts.status_code == 200
    assert len(attempts.json()) == 1
    assert "recipient" not in attempts.text.lower()

    engine = create_engine(url)
    with Session(engine) as session:
        assert session.scalar(select(OutboundSendAttempt)).status == OutboundSendStatus.SENT
