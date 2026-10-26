from __future__ import annotations

from email import message_from_bytes
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from test_campaigns import NOW, SECRET, seed_campaign
from test_outbound import FakeSender, approved_draft

from marketingiq.api.app import create_app
from marketingiq.application.errors import ConflictError
from marketingiq.application.outbound import OutboundSenderRegistry, OutboundSendService
from marketingiq.application.tenant import TenantContext
from marketingiq.application.unsubscribe import (
    InvalidUnsubscribeToken,
    UnsubscribeLinks,
    UnsubscribeRecipient,
    record_opt_out,
)
from marketingiq.domain.models import Base, MembershipRole, Organization
from marketingiq.domain.outbound import OutboundMessage, SuppressionEntry, SuppressionSource
from marketingiq.infrastructure.outbound import SMTPEmailSender

BASE_URL = "https://marketingiq.example.test"


def links() -> UnsubscribeLinks:
    return UnsubscribeLinks(SECRET, BASE_URL)


def token_from(url: str) -> str:
    return parse_qs(urlsplit(url).query)["t"][0]


def test_tokens_round_trip_and_reject_tampering():
    signer = links()
    url = signer.url("org-1", " Buyer@Example.com ")

    assert url.startswith(BASE_URL + "/unsubscribe?t=")
    parsed = signer.parse(token_from(url))
    assert parsed == UnsubscribeRecipient("org-1", "buyer@example.com")

    token = token_from(url)
    _payload, signature = token.split(".")
    forged = signer.token("org-2", "buyer@example.com").split(".")[0] + "." + signature
    with pytest.raises(InvalidUnsubscribeToken):
        signer.parse(forged)
    with pytest.raises(InvalidUnsubscribeToken):
        signer.parse("not-a-token")
    with pytest.raises(InvalidUnsubscribeToken):
        UnsubscribeLinks("another-secret-that-is-long-enough-1234", BASE_URL).parse(token)


def test_record_opt_out_is_idempotent_and_blocks_future_sends(session):
    seeded = seed_campaign(session)
    draft, _approval = approved_draft(session, seeded)
    recipient = UnsubscribeRecipient(seeded["organization"].id, seeded["email"].email.lower())

    assert record_opt_out(session, recipient) is True
    assert record_opt_out(session, recipient) is False
    entry = session.scalar(select(SuppressionEntry))
    assert entry.source == SuppressionSource.OPT_OUT
    assert session.scalar(select(func.count()).select_from(SuppressionEntry)) == 1

    sender = FakeSender()
    outbound = OutboundSendService(
        session,
        TenantContext(
            seeded["organization"].id, seeded["user"].id, MembershipRole.MARKETING_USER
        ),
        OutboundSenderRegistry([sender]),
        NOW,
        unsubscribe_links=links(),
    )
    with pytest.raises(ConflictError, match="OUTBOUND_RECIPIENT_SUPPRESSED"):
        outbound.send(
            seeded["relationship"].id,
            draft.id,
            provider_key="SMTP",
            idempotency_key="send-key-unsub-1",
        )
    assert sender.calls == []


def test_record_opt_out_rejects_unknown_organization(session):
    with pytest.raises(InvalidUnsubscribeToken):
        record_opt_out(session, UnsubscribeRecipient("missing-org", "buyer@example.com"))


def test_sent_messages_carry_a_signed_unsubscribe_link(session):
    seeded = seed_campaign(session)
    draft, _approval = approved_draft(session, seeded)
    sender = FakeSender()
    outbound = OutboundSendService(
        session,
        TenantContext(
            seeded["organization"].id, seeded["user"].id, MembershipRole.MARKETING_USER
        ),
        OutboundSenderRegistry([sender]),
        NOW,
        unsubscribe_links=links(),
    )

    outbound.send(
        seeded["relationship"].id,
        draft.id,
        provider_key="SMTP",
        idempotency_key="send-key-unsub-2",
    )

    message = sender.calls[0]
    assert message.unsubscribe_url is not None
    parsed = links().parse(token_from(message.unsubscribe_url))
    assert parsed.organization_id == seeded["organization"].id
    assert parsed.email == seeded["email"].email.lower()


def test_smtp_sender_adds_one_click_headers_and_footer(monkeypatch):
    captured = {}

    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def ehlo(self):
            pass

        def starttls(self, **kwargs):
            pass

        def login(self, *args):
            pass

        def send_message(self, email):
            captured["raw"] = email.as_bytes()
            return {}

    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)
    sender = SMTPEmailSender(host="mail.example.test", from_email="sales@example.test")
    sender.send(
        OutboundMessage(
            recipient="buyer@example.com",
            subject="Hello",
            body="Body text",
            idempotency_key="send-key-unsub-3",
            unsubscribe_url=BASE_URL + "/unsubscribe?t=abc.def",
        )
    )

    parsed = message_from_bytes(captured["raw"])
    assert parsed["List-Unsubscribe"] == f"<{BASE_URL}/unsubscribe?t=abc.def>"
    assert parsed["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    body = parsed.get_payload(decode=True).decode()
    assert body.startswith("Body text")
    assert f"{BASE_URL}/unsubscribe?t=abc.def" in body


def test_unsubscribe_pages_confirm_and_one_click_post(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'unsub.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        org = Organization(name="Tenant", slug="tenant")
        session.add(org)
        session.commit()
        org_id = org.id
    client = TestClient(create_app(url, SECRET, BASE_URL))
    token = links().token(org_id, "buyer@example.com")

    page = client.get("/unsubscribe", params={"t": token})
    assert page.status_code == 200
    assert "bu***@example.com" in page.text
    assert "buyer@example.com" not in page.text

    assert client.get("/unsubscribe", params={"t": "bad.token"}).status_code == 404

    done = client.post(
        "/unsubscribe",
        params={"t": token},
        data={"List-Unsubscribe": "One-Click"},
    )
    assert done.status_code == 200
    assert client.post("/unsubscribe", params={"t": token}).status_code == 200
    with Session(engine) as session:
        entries = session.scalars(select(SuppressionEntry)).all()
        assert [(e.email_normalized, e.source) for e in entries] == [
            ("buyer@example.com", SuppressionSource.OPT_OUT)
        ]
