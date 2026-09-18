from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from test_campaigns import seed_campaign
from test_engagement import sent_attempt

from marketingiq.domain.engagement import (
    EngagementEventType,
    EngagementSource,
    OutreachEngagementEvent,
)
from marketingiq.domain.models import AuditLog
from marketingiq.domain.outbound import SuppressionEntry, SuppressionSource
from marketingiq.infrastructure.mailbox_engagement import (
    MailboxEngagementIngestor,
    MailboxMessage,
    parse_mailbox_outcome,
)

NOW = datetime(2026, 9, 17, 7, 0, tzinfo=UTC)


class FakeMailboxReader:
    configured = True
    identity = "test-mailbox"

    def __init__(self, messages):
        self.messages = messages

    def fetch_messages(self, limit):
        return self.messages[-limit:]


def _reply_raw() -> bytes:
    return b"""From: Buyer <buyer@example.test>
To: Marketing <marketing@example.test>
Message-ID: <reply-1@example.test>
In-Reply-To: <fake-message-id@example.test>
References: <fake-message-id@example.test>
Date: Thu, 17 Sep 2026 06:40:00 +0000
Subject: Re: MarketingIQ

This is a private reply body that must never be persisted.
"""


def _bounce_raw() -> bytes:
    return b"""From: Mail Delivery System <mailer-daemon@example.test>
To: sender@example.test
Message-ID: <bounce-1@example.test>
Date: Thu, 17 Sep 2026 06:45:00 +0000
Subject: Delivery Status Notification (Failure)
MIME-Version: 1.0
Content-Type: multipart/report; report-type=delivery-status; boundary="b"

--b
Content-Type: text/plain; charset=utf-8

Delivery failed.
--b
Content-Type: message/delivery-status

Action: failed
Status: 5.1.1
Original-Message-ID: <fake-message-id@example.test>

--b--
"""


def test_mailbox_reply_is_ingested_without_reply_body(session):
    seeded = seed_campaign(session)
    _draft, attempt = sent_attempt(session, seeded)
    reader = FakeMailboxReader([MailboxMessage("101", _reply_raw())])

    result = MailboxEngagementIngestor(session, reader, now=NOW).run()

    assert result.processed == 1
    assert result.matched == 1
    assert result.replies == 1
    assert result.bounces == 0
    event = session.scalar(select(OutreachEngagementEvent))
    assert event is not None
    assert event.send_attempt_id == attempt.id
    assert event.event_type == EngagementEventType.REPLIED
    assert event.source == EngagementSource.PROVIDER
    assert event.provider_key == "SMTP"
    assert event.provider_event_id.startswith("imap:")
    assert event.recorded_by_user_id is None
    assert "private reply body" not in str(event.metadata_json).lower()

    audit = session.scalar(
        select(AuditLog).where(AuditLog.action == "outreach_engagement.provider_ingested")
    )
    assert audit is not None
    assert "private reply body" not in str(audit.metadata_json).lower()


def test_mailbox_ingestion_is_idempotent_for_same_uid(session):
    seeded = seed_campaign(session)
    sent_attempt(session, seeded)
    reader = FakeMailboxReader([MailboxMessage("101", _reply_raw())])
    ingestor = MailboxEngagementIngestor(session, reader, now=NOW)

    first = ingestor.run()
    second = ingestor.run()

    assert first.replies == 1
    assert second.replies == 1
    events = list(session.scalars(select(OutreachEngagementEvent)))
    assert len(events) == 1


def test_dsn_failure_records_bounce_and_suppresses_recipient(session):
    seeded = seed_campaign(session)
    _draft, attempt = sent_attempt(session, seeded)
    reader = FakeMailboxReader([MailboxMessage("202", _bounce_raw())])

    result = MailboxEngagementIngestor(session, reader, now=NOW).run()

    assert result.bounces == 1
    event = session.scalar(select(OutreachEngagementEvent))
    assert event is not None
    assert event.send_attempt_id == attempt.id
    assert event.event_type == EngagementEventType.BOUNCED
    assert event.reason_code == "DSN_5_1_1"

    suppression = session.scalar(select(SuppressionEntry))
    assert suppression is not None
    assert suppression.source == SuppressionSource.BOUNCE
    assert suppression.created_by_user_id is None


def test_automatic_reply_is_not_counted_as_human_reply(session):
    seeded = seed_campaign(session)
    sent_attempt(session, seeded)
    raw = _reply_raw().replace(
        b"Subject: Re: MarketingIQ\n",
        b"Auto-Submitted: auto-replied\nSubject: Out of office\n",
    )
    reader = FakeMailboxReader([MailboxMessage("303", raw)])

    result = MailboxEngagementIngestor(session, reader, now=NOW).run()

    assert result.matched == 0
    assert result.skipped == 1
    assert session.scalar(select(OutreachEngagementEvent)) is None


def test_parser_requires_thread_reference_for_normal_reply():
    raw = b"""From: person@example.test
Message-ID: <standalone@example.test>
Date: Thu, 17 Sep 2026 06:50:00 +0000
Subject: Hello

No thread reference.
"""
    assert parse_mailbox_outcome(raw, now=NOW) is None
