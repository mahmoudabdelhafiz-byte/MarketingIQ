from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from test_campaigns import NOW, seed_campaign
from test_outbound import FakeSender, approved_draft, outbound_service

from marketingiq.application.errors import ConflictError
from marketingiq.application.follow_ups import (
    FollowUpExecutor,
    FollowUpService,
    SendWindow,
    compose_follow_up,
)
from marketingiq.application.outbound import OutboundSenderRegistry
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.engagement import (
    EngagementEventType,
    EngagementSource,
    OutreachEngagementEvent,
)
from marketingiq.domain.follow_ups import FollowUpStatus, OutboundFollowUp
from marketingiq.domain.models import (
    AuditLog,
    EmailVerificationStatus,
    MembershipRole,
    OrganizationMembership,
)
from marketingiq.domain.outbound import OutboundSendResult, SuppressionEntry, SuppressionSource

ALWAYS_OPEN = SendWindow(UTC, 0, 24, frozenset(range(7)))


class ThreadSender(FakeSender):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.counter = 0

    def send(self, message):
        self.calls.append(message)
        if self.error:
            raise self.error
        self.counter += 1
        return OutboundSendResult("SMTP", True, f"<msg-{self.counter}@example.test>")


def sent_email(session, sender, role=MembershipRole.MARKETING_USER):
    seeded = seed_campaign(session, role=role)
    session.add(
        OrganizationMembership(
            organization_id=seeded["organization"].id, user_id=seeded["user"].id, role=role
        )
    )
    session.flush()
    draft, _approval = approved_draft(session, seeded)
    attempt = outbound_service(session, seeded, sender, role=role).send(
        seeded["relationship"].id, draft.id, provider_key="SMTP", idempotency_key="send-key-0001"
    )
    return seeded, draft, attempt


def follow_ups(session, seeded, sender, role=MembershipRole.MARKETING_USER, now=NOW):
    return FollowUpService(
        session,
        TenantContext(seeded["organization"].id, seeded["user"].id, role),
        OutboundSenderRegistry([sender]),
        now=now,
        window=ALWAYS_OPEN,
    )


def executor(session, sender, now, window=ALWAYS_OPEN):
    return FollowUpExecutor(session, OutboundSenderRegistry([sender]), now=now, window=window)


def record(session, seeded, attempt, event_type):
    session.add(
        OutreachEngagementEvent(
            organization_id=seeded["organization"].id,
            organization_company_id=seeded["relationship"].id,
            company_id=seeded["company"].id,
            send_attempt_id=attempt.id,
            draft_id=attempt.draft_id,
            contact_id=attempt.contact_id,
            contact_email_id=attempt.contact_email_id,
            event_type=event_type,
            source=EngagementSource.MANUAL,
            event_key=f"evt-{event_type.value}-{attempt.id}"[:100],
            occurred_at=NOW + timedelta(days=1),
        )
    )
    session.flush()


def test_schedule_creates_two_threaded_follow_ups(session):
    sender = ThreadSender()
    seeded, draft, attempt = sent_email(session, sender)
    created = follow_ups(session, seeded, sender).schedule(seeded["relationship"].id, attempt.id)

    assert [item.step for item in created] == [1, 2]
    sent_at = attempt.completed_at
    if sent_at.tzinfo is None:
        sent_at = sent_at.replace(tzinfo=UTC)
    assert [item.due_at for item in created] == [
        sent_at + timedelta(days=3),
        sent_at + timedelta(days=7),
    ]
    assert all(item.subject.startswith("Re: ") for item in created)
    assert "> " in created[0].body and seeded["product"].name in created[0].body
    assert all(item.status == FollowUpStatus.SCHEDULED and not item.auto_send for item in created)

    # Scheduling again is idempotent.
    assert follow_ups(session, seeded, sender).schedule(seeded["relationship"].id, attempt.id) == []


def test_approval_mode_queues_then_person_sends_in_thread(session):
    sender = ThreadSender()
    seeded, draft, attempt = sent_email(session, sender)
    svc = follow_ups(session, seeded, sender)
    first, second = svc.schedule(seeded["relationship"].id, attempt.id)

    counts = executor(session, sender, NOW + timedelta(days=3, hours=1)).run_due()
    assert counts["awaiting_approval"] == 1 and counts["sent"] == 0
    assert first.status == FollowUpStatus.AWAITING_APPROVAL
    assert second.status == FollowUpStatus.SCHEDULED

    with pytest.raises(ConflictError, match="EARLIER_STEP_PENDING"):
        svc.send_now(second.id)

    svc.send_now(first.id)
    assert first.status == FollowUpStatus.SENT
    message = sender.calls[-1]
    assert message.in_reply_to == attempt.provider_message_id
    assert message.references == (attempt.provider_message_id,)
    assert message.recipient == seeded["email"].email

    executor(session, sender, NOW + timedelta(days=7, hours=1)).run_due()
    svc.send_now(second.id)
    assert sender.calls[-1].references == (attempt.provider_message_id, first.provider_message_id)


def test_auto_mode_sends_and_stops_on_reply(session):
    sender = ThreadSender()
    seeded, draft, attempt = sent_email(session, sender)
    first, second = follow_ups(session, seeded, sender).schedule(
        seeded["relationship"].id, attempt.id, auto_send=True
    )
    calls_before = len(sender.calls)

    assert executor(session, sender, NOW + timedelta(days=3, hours=1)).run_due()["sent"] == 1
    assert first.status == FollowUpStatus.SENT and len(sender.calls) == calls_before + 1

    record(session, seeded, attempt, EngagementEventType.REPLIED)
    counts = executor(session, sender, NOW + timedelta(days=7, hours=1)).run_due()
    assert counts["skipped"] == 1
    assert second.status == FollowUpStatus.SKIPPED
    assert second.stop_reason == "CONVERSATION_REPLIED"
    assert len(sender.calls) == calls_before + 1


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ("bounce", "CONVERSATION_BOUNCED"),
        ("opt_out", "CONVERSATION_OPT_OUT"),
        ("suppressed", "RECIPIENT_SUPPRESSED"),
        ("unverified", "EMAIL_NOT_VERIFIED"),
        ("excluded", "CONTACT_EXCLUDED"),
    ],
)
def test_every_stop_rule_prevents_sending(session, change, reason):
    sender = ThreadSender()
    seeded, draft, attempt = sent_email(session, sender)
    first, _second = follow_ups(session, seeded, sender).schedule(
        seeded["relationship"].id, attempt.id, auto_send=True
    )
    calls_before = len(sender.calls)
    if change == "bounce":
        record(session, seeded, attempt, EngagementEventType.BOUNCED)
    elif change == "opt_out":
        record(session, seeded, attempt, EngagementEventType.OPT_OUT)
    elif change == "suppressed":
        session.add(
            SuppressionEntry(
                organization_id=seeded["organization"].id,
                email_normalized=seeded["email"].email.lower(),
                source=SuppressionSource.MANUAL,
                created_by_user_id=seeded["user"].id,
                created_at=NOW,
            )
        )
    elif change == "unverified":
        seeded["email"].verification_status = EmailVerificationStatus.INVALID
    elif change == "excluded":
        seeded["contact"].review_status = "EXCLUDED"
    session.flush()

    executor(session, sender, NOW + timedelta(days=3, hours=1)).run_due()

    assert first.status == FollowUpStatus.SKIPPED
    assert first.stop_reason == reason
    assert len(sender.calls) == calls_before


def test_cannot_schedule_after_the_conversation_ended(session):
    sender = ThreadSender()
    seeded, draft, attempt = sent_email(session, sender)
    record(session, seeded, attempt, EngagementEventType.POSITIVE_REPLY)
    with pytest.raises(ConflictError, match="CONVERSATION_ALREADY_ENDED"):
        follow_ups(session, seeded, sender).schedule(seeded["relationship"].id, attempt.id)


def test_send_window_and_daily_cap_defer_automatic_sends(session, monkeypatch):
    sender = ThreadSender()
    seeded, draft, attempt = sent_email(session, sender)
    first, _ = follow_ups(session, seeded, sender).schedule(
        seeded["relationship"].id, attempt.id, auto_send=True
    )
    office = SendWindow(UTC, 9, 17, frozenset({0, 1, 2, 3, 4}))  # Mon–Fri 09–17 UTC
    saturday_night = datetime(2026, 9, 19, 22, 0, tzinfo=UTC)
    calls_before = len(sender.calls)

    counts = executor(session, sender, saturday_night, window=office).run_due()
    assert counts["deferred"] == 1 and len(sender.calls) == calls_before
    assert first.due_at == datetime(2026, 9, 21, 9, 0, tzinfo=UTC)  # Monday 09:00

    monkeypatch.setenv("FOLLOW_UP_DAILY_LIMIT", "1")
    monday = datetime(2026, 9, 21, 10, 0, tzinfo=UTC)
    executor(session, sender, monday, window=office).run_due()
    assert first.status == FollowUpStatus.SENT

    second = session.scalar(
        select(OutboundFollowUp).where(
            OutboundFollowUp.initial_attempt_id == attempt.id, OutboundFollowUp.step == 2
        )
    )
    second.due_at = monday + timedelta(minutes=30)
    session.flush()
    calls = len(sender.calls)
    counts = executor(session, sender, monday + timedelta(hours=1), window=office).run_due()
    assert counts["deferred"] == 1 and len(sender.calls) == calls
    assert second.status == FollowUpStatus.SCHEDULED
    assert second.due_at == datetime(2026, 9, 22, 9, 0, tzinfo=UTC)  # next day's opening


def test_auto_send_falls_back_to_approval_when_scheduler_lost_permission(session):
    sender = ThreadSender()
    seeded, draft, attempt = sent_email(session, sender)
    first, _ = follow_ups(session, seeded, sender).schedule(
        seeded["relationship"].id, attempt.id, auto_send=True
    )
    membership = session.scalar(
        select(OrganizationMembership).where(
            OrganizationMembership.user_id == seeded["user"].id
        )
    )
    membership.role = MembershipRole.READ_ONLY
    session.flush()
    calls_before = len(sender.calls)
    executor(session, sender, NOW + timedelta(days=3, hours=1)).run_due()
    assert first.status == FollowUpStatus.AWAITING_APPROVAL
    assert len(sender.calls) == calls_before


def test_edit_skip_and_tenant_isolation(session):
    sender = ThreadSender()
    seeded, draft, attempt = sent_email(session, sender)
    svc = follow_ups(session, seeded, sender)
    first, second = svc.schedule(seeded["relationship"].id, attempt.id)

    svc.edit(first.id, subject="Re: quick question", body="Hi Ada,\n\nAny thoughts?")
    assert first.subject == "Re: quick question"
    skipped = svc.skip(first.id, cancel_sequence=True)
    assert {item.id for item in skipped} == {first.id, second.id}
    assert all(item.stop_reason == "SKIPPED_BY_USER" for item in skipped)
    with pytest.raises(ConflictError):
        svc.send_now(first.id)

    other_sender = ThreadSender()
    other, _d, _a = sent_email(session, other_sender)
    outsider = follow_ups(session, other, other_sender)
    assert all(item["id"] not in {first.id, second.id} for item in outsider.list())
    from marketingiq.application.errors import NotFoundError

    with pytest.raises(NotFoundError):
        outsider.send_now(first.id)

    actions = set(session.scalars(select(AuditLog.action)))
    assert {"follow_up.scheduled", "follow_up.edited", "follow_up.skipped"} <= actions
    metadata = " ".join(
        str(row.metadata_json)
        for row in session.scalars(select(AuditLog).where(AuditLog.action.like("follow_up.%")))
    )
    assert seeded["email"].email not in metadata


def test_compose_has_no_new_claims():
    class P:
        name = "CardIQ"

    subject, body = compose_follow_up(
        2,
        first_name=" Sara ",
        product=P,
        original_subject="CardIQ for Port Alpha",
        original_body="Hi Sara,\n\nOriginal text.",
        original_sent_at=NOW,
    )
    assert subject == "Re: CardIQ for Port Alpha"
    assert body.startswith("Hi Sara,")
    assert "last note" in body
    assert "> Original text." in body
    assert compose_follow_up(
        1, first_name=None, product=P, original_subject="Re: x", original_body="y",
        original_sent_at=NOW,
    )[0] == "Re: x"


def test_follow_up_rows_are_unique_per_step(session):
    sender = ThreadSender()
    seeded, draft, attempt = sent_email(session, sender)
    follow_ups(session, seeded, sender).schedule(seeded["relationship"].id, attempt.id)
    rows = session.scalars(
        select(OutboundFollowUp).where(OutboundFollowUp.initial_attempt_id == attempt.id)
    ).all()
    assert sorted(row.step for row in rows) == [1, 2]
