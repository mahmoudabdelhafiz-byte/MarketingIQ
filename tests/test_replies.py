"""Reply assistant: capture from the mailbox, suggestions, answers in the thread, outcomes."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from test_campaigns import seed_campaign
from test_follow_ups import ALWAYS_OPEN, ThreadSender, sent_email
from test_mailbox_engagement import FakeMailboxReader

from marketingiq.application.errors import ConflictError, NotFoundError
from marketingiq.application.outbound import OutboundSenderRegistry
from marketingiq.application.replies import (
    ReplyAIError,
    ReplyAIResult,
    ReplyAssistantService,
)
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.engagement import EngagementEventType, OutreachEngagementEvent
from marketingiq.domain.models import MembershipRole
from marketingiq.domain.outbound import SuppressionEntry
from marketingiq.domain.pipeline import OpportunityStage, SalesOpportunity
from marketingiq.domain.replies import InboundReply, ReplyIntent, ReplyStatus
from marketingiq.infrastructure.mailbox_engagement import (
    MailboxEngagementIngestor,
    MailboxMessage,
)

NOW = datetime(2026, 11, 16, 6, 0, tzinfo=UTC)  # Monday 09:00 Riyadh


def reply_raw(in_reply_to: str, text: str, message_id: str = "<reply-1@buyer.test>") -> bytes:
    return (
        f"From: Ava Buyer <ava@buyer.test>\r\n"
        f"Message-ID: {message_id}\r\n"
        f"In-Reply-To: {in_reply_to}\r\n"
        f"References: {in_reply_to}\r\n"
        f"Date: Mon, 16 Nov 2026 05:30:00 +0000\r\n"
        f"Subject: Re: MarketingIQ for Acme Logistics\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n\r\n"
        f"{text}\r\n\r\nOn Fri, 13 Nov 2026, Seller wrote:\r\n> earlier text\r\n"
    ).encode()


def ingest(session, *raws):
    reader = FakeMailboxReader([MailboxMessage(str(100 + i), raw) for i, raw in enumerate(raws)])
    return MailboxEngagementIngestor(session, reader, now=NOW).run()


def assistant(session, seeded, sender, ai=None, role=MembershipRole.MARKETING_USER):
    return ReplyAssistantService(
        session,
        TenantContext(seeded["organization"].id, seeded["user"].id, role),
        OutboundSenderRegistry([sender]),
        ai=ai,
        now=NOW,
        window=ALWAYS_OPEN,
    )


def captured(session, text="Sounds good, can we have a call next week?"):
    sender = ThreadSender()
    seeded, draft, attempt = sent_email(session, sender)
    result = ingest(session, reply_raw(attempt.provider_message_id, text))
    assert result.replies == 1
    reply = session.scalar(select(InboundReply))
    return sender, seeded, attempt, reply


def test_mailbox_captures_reply_with_intent_and_suggestion(session):
    _sender, _seeded, attempt, reply = captured(session)

    assert reply.initial_attempt_id == attempt.id
    assert reply.body_text == "Sounds good, can we have a call next week?"  # quote removed
    assert reply.from_email == "ava@buyer.test"
    assert reply.intent == ReplyIntent.MEETING_REQUEST and reply.intent_source == "RULES"
    assert reply.status == ReplyStatus.NEW
    assert len(reply.suggested_slots) == 2
    assert reply.suggested_subject == "Re: MarketingIQ for Acme Logistics"
    assert reply.suggested_body.startswith("Hi Ava,") and "call" in reply.suggested_body

    # Scanning the same message again does not duplicate it.
    ingest(session, reply_raw(attempt.provider_message_id, "again"))
    assert len(list(session.scalars(select(InboundReply)))) == 1


def test_capture_can_be_switched_off(session, monkeypatch):
    monkeypatch.setenv("REPLY_CAPTURE_ENABLED", "false")
    sender = ThreadSender()
    _seeded, _draft, attempt = sent_email(session, sender)
    assert ingest(session, reply_raw(attempt.provider_message_id, "Yes")).replies == 1
    assert session.scalar(select(InboundReply)) is None


class FakeReplyAI:
    model = "fake-reply"
    configured = True

    def __init__(self, body="Hi Ava,\n\nIt is 99 SAR per user.\n\nBest regards,", error=None):
        self.body = body
        self.error = error
        self.requests = []

    def suggest(self, request):
        self.requests.append(request)
        if self.error:
            raise ReplyAIError(self.error)
        return ReplyAIResult(
            intent=ReplyIntent.QUESTION,
            confidence=0.9,
            summary="Asks about price",
            subject="Re: MarketingIQ for Acme Logistics",
            body=self.body,
            model=self.model,
        )


def test_ai_suggestion_uses_knowledge_base(session):
    sender, seeded, _attempt, reply = captured(session, "How much is it per user?")
    seeded["product"].knowledge_base = "Pricing: 99 SAR per user per year."
    seeded["product"].booking_url = "https://cal.example/demo"
    ai = FakeReplyAI()

    item = assistant(session, seeded, sender, ai).suggest(reply.id)

    request = ai.requests[0]
    assert request.knowledge_base.startswith("Pricing") and request.booking_url
    assert len(request.proposed_times) == 2 and request.reply_text.startswith("How much")
    assert (item.intent, item.intent_source, item.summary) == (
        ReplyIntent.QUESTION,
        "AI",
        "Asks about price",
    )
    assert "99 SAR" in item.suggested_body


@pytest.mark.parametrize(
    "ai",
    [
        FakeReplyAI(body="Only $49 this week! https://evil.example"),
        FakeReplyAI(error="TIMEOUT"),
    ],
)
def test_rejected_or_failed_ai_falls_back_to_template(session, ai):
    sender, seeded, _attempt, reply = captured(session, "How much is it per user?")
    item = assistant(session, seeded, sender, ai).suggest(reply.id)
    assert item.intent_source == "RULES" and item.suggestion_model is None
    assert "confirm the details" in item.suggested_body


def test_send_answers_in_thread_once(session):
    sender, seeded, attempt, reply = captured(session)
    svc = assistant(session, seeded, sender)

    item = svc.send(reply.id, subject="Re: MarketingIQ", body="Hi Ava,\n\nTuesday 10:00 works.")

    message = sender.calls[-1]
    assert message.in_reply_to == "<reply-1@buyer.test>"
    assert message.references == (attempt.provider_message_id, "<reply-1@buyer.test>")
    assert item.status == ReplyStatus.ANSWERED and item.answer_message_id == "<msg-2@example.test>"
    with pytest.raises(ConflictError, match="REPLY_ALREADY_HANDLED"):
        svc.send(reply.id, subject="Re: x", body="again")

    # Their next reply, to our answer, is matched back to the same conversation.
    ingest(
        session,
        reply_raw("<msg-2@example.test>", "Great, see you then.", "<reply-2@buyer.test>"),
    )
    replies = list(session.scalars(select(InboundReply).order_by(InboundReply.created_at)))
    assert len(replies) == 2 and replies[1].initial_attempt_id == attempt.id


def test_send_blocked_for_suppressed_recipient(session):
    sender, seeded, _attempt, reply = captured(session)
    svc = assistant(session, seeded, sender)
    svc.unsubscribe(reply.id)
    assert session.scalar(select(SuppressionEntry)) is not None
    assert reply.status == ReplyStatus.DISMISSED and reply.outcome == "UNSUBSCRIBED"
    with pytest.raises(ConflictError, match="REPLY_ALREADY_HANDLED"):
        svc.send(reply.id, subject="Re: x", body="y")


def test_send_then_unsubscribe(session):
    sender, seeded, _attempt, reply = captured(session, "Please unsubscribe me.")
    assert reply.intent == ReplyIntent.UNSUBSCRIBE
    item = assistant(session, seeded, sender).send(
        reply.id, subject="Re: x", body="Done, you are removed.", then_unsubscribe=True
    )
    assert sender.calls[-1].unsubscribe_url is None
    assert item.outcome == "UNSUBSCRIBED"
    assert session.scalar(select(SuppressionEntry)) is not None


def test_meeting_booked_moves_pipeline_and_records_positive_reply(session):
    sender, seeded, attempt, reply = captured(session)
    assistant(session, seeded, sender).mark_meeting_booked(reply.id, "Tuesday 10:00")

    events = {
        event.event_type
        for event in session.scalars(
            select(OutreachEngagementEvent).where(
                OutreachEngagementEvent.send_attempt_id == attempt.id
            )
        )
    }
    assert {EngagementEventType.REPLIED, EngagementEventType.POSITIVE_REPLY} <= events
    opportunity = session.scalar(
        select(SalesOpportunity).where(SalesOpportunity.send_attempt_id == attempt.id)
    )
    assert opportunity.stage == OpportunityStage.MEETING
    assert reply.outcome == "MEETING_BOOKED"


def test_not_interested_closes_opportunity(session):
    sender, seeded, attempt, reply = captured(session, "Thanks, not interested.")
    assistant(session, seeded, sender).mark_not_interested(reply.id)
    opportunity = session.scalar(
        select(SalesOpportunity).where(SalesOpportunity.send_attempt_id == attempt.id)
    )
    assert opportunity.stage == OpportunityStage.LOST


def test_replies_are_tenant_isolated_and_listed(session):
    sender, seeded, _attempt, reply = captured(session)
    listed = assistant(session, seeded, sender).list(ReplyStatus.NEW)
    assert [item["id"] for item in listed] == [reply.id]
    assert listed[0]["suggested_slots"][0]["label"]

    other = seed_campaign(session)
    with pytest.raises(NotFoundError):
        assistant(session, other, sender).get(reply.id)


def test_read_only_cannot_send(session):
    from marketingiq.application.errors import AuthorizationError

    sender, seeded, _attempt, reply = captured(session)
    with pytest.raises(AuthorizationError):
        assistant(session, seeded, sender, role=MembershipRole.READ_ONLY).send(
            reply.id, subject="Re: x", body="y"
        )
