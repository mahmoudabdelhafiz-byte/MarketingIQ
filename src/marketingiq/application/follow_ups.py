"""Follow-up sequences: up to two short emails in the same thread when there is no reply.

Safety rules, checked again right before every send:
- stop on any reply, bounce, complaint or opt-out recorded for the original email;
- stop if the address is suppressed, no longer verified, or the person was excluded;
- automatic sends only inside the send window and under a daily per-organization cap;
- the person who scheduled the sequence must still be allowed to send email.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from datetime import time as day_time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from marketingiq.application.authorization import (
    ROLE_PERMISSIONS,
    Permission,
    require_permission,
)
from marketingiq.application.decision_makers import targeting_block_reason
from marketingiq.application.errors import ConflictError, NotFoundError
from marketingiq.application.outbound import OutboundSenderRegistry
from marketingiq.application.tenant import TenantContext
from marketingiq.application.unsubscribe import UnsubscribeLinks
from marketingiq.domain.campaigns import CampaignDraft, CampaignDraftReviewEvent
from marketingiq.domain.engagement import OutreachEngagementEvent
from marketingiq.domain.follow_ups import (
    ACTIVE_FOLLOW_UP_STATUSES,
    FollowUpStatus,
    OutboundFollowUp,
)
from marketingiq.domain.models import (
    AuditLog,
    Company,
    ContactCandidate,
    ContactEmail,
    EmailVerificationStatus,
    OrganizationCompany,
    OrganizationMembership,
    Product,
    User,
)
from marketingiq.domain.outbound import (
    OutboundMessage,
    OutboundProviderError,
    OutboundSendAttempt,
    OutboundSendStatus,
    SuppressionEntry,
)

DEFAULT_DELAYS_DAYS = (3, 7)
MAX_STEPS = 2
WEEKDAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


@dataclass(frozen=True)
class SendWindow:
    """When automatic follow-ups may be sent (manual approvals send immediately)."""

    timezone: Any
    start_hour: int
    end_hour: int
    weekdays: frozenset[int]

    @classmethod
    def from_env(cls) -> SendWindow:
        name = os.environ.get("FOLLOW_UP_TIMEZONE", "Asia/Riyadh").strip() or "Asia/Riyadh"
        try:
            zone: Any = ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            zone = UTC
        start, end = 9, 17
        raw_hours = os.environ.get("FOLLOW_UP_SEND_HOURS", "9-17").strip()
        try:
            first, last = (int(part) for part in raw_hours.split("-", 1))
            if 0 <= first < last <= 24:
                start, end = first, last
        except ValueError:
            pass
        raw_days = os.environ.get("FOLLOW_UP_SEND_DAYS", "sun,mon,tue,wed,thu").lower()
        days = frozenset(
            WEEKDAY_NAMES.index(part.strip()[:3])
            for part in raw_days.split(",")
            if part.strip()[:3] in WEEKDAY_NAMES
        ) or frozenset(range(7))
        return cls(zone, start, end, days)

    def allows(self, moment: datetime) -> bool:
        local = moment.astimezone(self.timezone)
        return local.weekday() in self.weekdays and self.start_hour <= local.hour < self.end_hour

    def next_opening(self, moment: datetime) -> datetime:
        local = moment.astimezone(self.timezone)
        for offset in range(8):
            day = (local + timedelta(days=offset)).date()
            opening = datetime.combine(day, day_time(self.start_hour), tzinfo=self.timezone)
            closing = datetime.combine(day, day_time(0), tzinfo=self.timezone) + timedelta(
                hours=self.end_hour
            )
            if day.weekday() not in self.weekdays or closing <= local:
                continue
            return max(opening, local).astimezone(UTC)
        return moment


def daily_limit() -> int:
    try:
        return max(1, int(os.environ.get("FOLLOW_UP_DAILY_LIMIT", "50")))
    except ValueError:
        return 50


def compose_follow_up(
    step: int,
    *,
    first_name: str | None,
    product: Product,
    original_subject: str,
    original_body: str,
    original_sent_at: datetime,
) -> tuple[str, str]:
    """Short, claim-free follow-up text. It repeats only the approved original."""

    greeting = f"Hi {first_name.strip()}," if first_name and first_name.strip() else "Hello,"
    subject = original_subject if original_subject.lower().startswith("re:") else (
        f"Re: {original_subject}"
    )[:255]
    if step == 1:
        lines = [
            greeting,
            f"I wanted to follow up on my note below about {product.name}, in case it got "
            "buried.",
            "Would it be worth a short conversation?",
            "Best regards,",
        ]
    else:
        lines = [
            greeting,
            "I don't want to fill your inbox, so this is my last note on this. If "
            f"{product.name} isn't a priority right now, no problem at all.",
            "If it is, just reply and I'll send a few times that suit you.",
            "Best regards,",
        ]
    quoted = "\n".join(f"> {line}" for line in original_body.strip().splitlines())
    sent = original_sent_at.astimezone(UTC).strftime("%d %b %Y")
    body = "\n\n".join(lines) + f"\n\n\nOn {sent}, I wrote:\n{quoted}"
    return subject, body


class FollowUpService:
    def __init__(
        self,
        session: Session,
        tenant: TenantContext,
        registry: OutboundSenderRegistry,
        *,
        now: datetime | None = None,
        unsubscribe_links: UnsubscribeLinks | None = None,
        window: SendWindow | None = None,
    ) -> None:
        self.session = session
        self.tenant = tenant
        self.registry = registry
        self.now = now or datetime.now(UTC)
        self.unsubscribe_links = unsubscribe_links
        self.window = window or SendWindow.from_env()

    # ------------------------------------------------------------------ scheduling

    def schedule(
        self,
        relationship_id: str,
        attempt_id: str,
        *,
        auto_send: bool = False,
        delays_days: tuple[int, ...] = DEFAULT_DELAYS_DAYS,
    ) -> list[OutboundFollowUp]:
        require_permission(self.tenant, Permission.SEND_OUTBOUND_EMAIL)
        if not 1 <= len(delays_days) <= MAX_STEPS or any(
            not 1 <= d <= 30 for d in delays_days
        ) or list(delays_days) != sorted(set(delays_days)):
            raise ValueError("Follow-up delays must be 1 or 2 increasing values of 1–30 days")
        attempt = self._attempt(relationship_id, attempt_id)
        if attempt.status != OutboundSendStatus.SENT or attempt.completed_at is None:
            raise ConflictError("FOLLOW_UP_ORIGINAL_NOT_SENT")
        if self._stop_reason_for_attempt(attempt):
            raise ConflictError("FOLLOW_UP_CONVERSATION_ALREADY_ENDED")
        existing = {
            item.step: item
            for item in self.session.scalars(
                select(OutboundFollowUp).where(OutboundFollowUp.initial_attempt_id == attempt.id)
            )
        }
        approval = self.session.get(CampaignDraftReviewEvent, attempt.review_event_id)
        draft = self.session.get(CampaignDraft, attempt.draft_id)
        contact = self.session.get(ContactCandidate, attempt.contact_id)
        product = self.session.get(Product, draft.product_id) if draft else None
        if approval is None or draft is None or contact is None or product is None:
            raise NotFoundError("Original email details not found")
        sent_at = _aware(attempt.completed_at)
        created: list[OutboundFollowUp] = []
        for step, delay in enumerate(delays_days, start=1):
            if step in existing:
                continue
            subject, body = compose_follow_up(
                step,
                first_name=contact.first_name,
                product=product,
                original_subject=approval.subject,
                original_body=approval.body,
                original_sent_at=sent_at,
            )
            item = OutboundFollowUp(
                organization_id=self.tenant.organization_id,
                organization_company_id=attempt.organization_company_id,
                company_id=attempt.company_id,
                initial_attempt_id=attempt.id,
                draft_id=attempt.draft_id,
                contact_id=attempt.contact_id,
                contact_email_id=attempt.contact_email_id,
                step=step,
                status=FollowUpStatus.SCHEDULED,
                auto_send=auto_send,
                due_at=sent_at + timedelta(days=delay),
                subject=subject,
                body=body,
                created_by_user_id=self.tenant.actor_user_id,
                created_at=self.now,
            )
            self.session.add(item)
            created.append(item)
        self.session.flush()
        if created:
            self._audit(
                "follow_up.scheduled",
                created[0],
                {"steps": [item.step for item in created], "auto_send": auto_send},
            )
        return created

    # ------------------------------------------------------------------ queue

    def list(
        self, *, statuses: tuple[FollowUpStatus, ...] | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        require_permission(self.tenant, Permission.READ)
        query = select(OutboundFollowUp).where(
            OutboundFollowUp.organization_id == self.tenant.organization_id
        )
        if statuses:
            query = query.where(OutboundFollowUp.status.in_(statuses))
        items = list(
            self.session.scalars(
                query.order_by(OutboundFollowUp.due_at.desc(), OutboundFollowUp.step).limit(
                    max(1, min(limit, 500))
                )
            )
        )
        return [self.describe(item) for item in items]

    def for_attempt(self, relationship_id: str, attempt_id: str) -> list[dict[str, Any]]:
        require_permission(self.tenant, Permission.READ)
        attempt = self._attempt(relationship_id, attempt_id)
        items = self.session.scalars(
            select(OutboundFollowUp)
            .where(OutboundFollowUp.initial_attempt_id == attempt.id)
            .order_by(OutboundFollowUp.step)
        )
        return [self.describe(item) for item in items]

    def describe(self, item: OutboundFollowUp) -> dict[str, Any]:
        contact = self.session.get(ContactCandidate, item.contact_id)
        company = self.session.get(Company, item.company_id)
        email = self.session.get(ContactEmail, item.contact_email_id)
        name = None
        if contact is not None:
            name = contact.full_name or " ".join(
                part for part in (contact.first_name, contact.last_name) if part
            )
        return {
            "id": item.id,
            "organization_company_id": item.organization_company_id,
            "company_name": company.canonical_name if company else None,
            "contact_id": item.contact_id,
            "contact_name": name or None,
            "recipient": email.email if email else None,
            "draft_id": item.draft_id,
            "initial_attempt_id": item.initial_attempt_id,
            "step": item.step,
            "status": item.status,
            "auto_send": item.auto_send,
            "due_at": item.due_at,
            "subject": item.subject,
            "body": item.body,
            "sent_at": item.sent_at,
            "stop_reason": item.stop_reason,
            "provider_message_id": item.provider_message_id,
        }

    def edit(self, follow_up_id: str, *, subject: str, body: str) -> OutboundFollowUp:
        require_permission(self.tenant, Permission.REVIEW_CAMPAIGN_DRAFT)
        item = self._follow_up(follow_up_id)
        if item.status not in ACTIVE_FOLLOW_UP_STATUSES:
            raise ConflictError("FOLLOW_UP_NOT_EDITABLE")
        clean_subject, clean_body = subject.strip(), body.strip()
        if not (0 < len(clean_subject) <= 255 and 0 < len(clean_body) <= 20000):
            raise ValueError("Subject (1–255 chars) and body (1–20000 chars) are required")
        item.subject, item.body = clean_subject, clean_body
        self._audit("follow_up.edited", item, {})
        return item

    def skip(self, follow_up_id: str, *, cancel_sequence: bool = False) -> list[OutboundFollowUp]:
        require_permission(self.tenant, Permission.SEND_OUTBOUND_EMAIL)
        item = self._follow_up(follow_up_id)
        targets = [item]
        if cancel_sequence:
            targets = list(
                self.session.scalars(
                    select(OutboundFollowUp).where(
                        OutboundFollowUp.initial_attempt_id == item.initial_attempt_id,
                        OutboundFollowUp.status.in_(ACTIVE_FOLLOW_UP_STATUSES),
                    )
                )
            )
        changed = []
        for target in targets:
            if target.status in ACTIVE_FOLLOW_UP_STATUSES:
                self._stop(target, "SKIPPED_BY_USER")
                target.decided_by_user_id = self.tenant.actor_user_id
                changed.append(target)
        return changed

    def send_now(self, follow_up_id: str) -> OutboundFollowUp:
        """A person approves and sends a follow-up immediately (no send window)."""

        require_permission(self.tenant, Permission.SEND_OUTBOUND_EMAIL)
        item = self._follow_up(follow_up_id)
        if item.status not in ACTIVE_FOLLOW_UP_STATUSES:
            raise ConflictError("FOLLOW_UP_NOT_PENDING")
        earlier = self.session.scalar(
            select(OutboundFollowUp.id).where(
                OutboundFollowUp.initial_attempt_id == item.initial_attempt_id,
                OutboundFollowUp.step < item.step,
                OutboundFollowUp.status.in_(ACTIVE_FOLLOW_UP_STATUSES),
            )
        )
        if earlier is not None:
            raise ConflictError("FOLLOW_UP_EARLIER_STEP_PENDING")
        item.decided_by_user_id = self.tenant.actor_user_id
        return _deliver(self.session, item, self.registry, self.now, self.unsubscribe_links)

    # ------------------------------------------------------------------ helpers

    def _attempt(self, relationship_id: str, attempt_id: str) -> OutboundSendAttempt:
        relationship = self.session.scalar(
            select(OrganizationCompany).where(
                OrganizationCompany.id == relationship_id,
                OrganizationCompany.organization_id == self.tenant.organization_id,
            )
        )
        attempt = self.session.scalar(
            select(OutboundSendAttempt).where(
                OutboundSendAttempt.id == attempt_id,
                OutboundSendAttempt.organization_id == self.tenant.organization_id,
            )
        )
        if relationship is None or attempt is None or (
            attempt.organization_company_id != relationship.id
        ):
            raise NotFoundError("Sent email not found")
        return attempt

    def _follow_up(self, follow_up_id: str) -> OutboundFollowUp:
        item = self.session.scalar(
            select(OutboundFollowUp).where(
                OutboundFollowUp.id == follow_up_id,
                OutboundFollowUp.organization_id == self.tenant.organization_id,
            )
        )
        if item is None:
            raise NotFoundError("Follow-up not found")
        return item

    def _stop_reason_for_attempt(self, attempt: OutboundSendAttempt) -> str | None:
        return _conversation_stop_reason(self.session, attempt.id)

    def _stop(self, item: OutboundFollowUp, reason: str) -> None:
        item.status = FollowUpStatus.SKIPPED
        item.stop_reason = reason[:50]
        _audit(
            self.session, "follow_up.skipped", item, self.tenant.actor_user_id, {"reason": reason}
        )

    def _audit(self, action: str, item: OutboundFollowUp, extra: dict) -> None:
        _audit(self.session, action, item, self.tenant.actor_user_id, extra)


class FollowUpExecutor:
    """Cron entry point: moves due follow-ups forward across all organizations."""

    def __init__(
        self,
        session: Session,
        registry: OutboundSenderRegistry,
        *,
        now: datetime | None = None,
        unsubscribe_links: UnsubscribeLinks | None = None,
        window: SendWindow | None = None,
    ) -> None:
        self.session = session
        self.registry = registry
        self.now = now or datetime.now(UTC)
        self.unsubscribe_links = unsubscribe_links
        self.window = window or SendWindow.from_env()

    def run_due(self, limit: int = 100) -> dict[str, int]:
        counts = {"sent": 0, "awaiting_approval": 0, "skipped": 0, "failed": 0, "deferred": 0}
        due = list(
            self.session.scalars(
                select(OutboundFollowUp)
                .where(
                    OutboundFollowUp.status == FollowUpStatus.SCHEDULED,
                    OutboundFollowUp.due_at <= self.now,
                )
                .order_by(OutboundFollowUp.due_at, OutboundFollowUp.step)
                .limit(max(1, min(limit, 1000)))
            )
        )
        for item in due:
            reason = _stop_reason(self.session, item)
            if reason:
                item.status = FollowUpStatus.SKIPPED
                item.stop_reason = reason
                _audit(self.session, "follow_up.skipped", item, None, {"reason": reason})
                counts["skipped"] += 1
                continue
            earlier_pending = self.session.scalar(
                select(OutboundFollowUp.id).where(
                    OutboundFollowUp.initial_attempt_id == item.initial_attempt_id,
                    OutboundFollowUp.step < item.step,
                    OutboundFollowUp.status.in_(ACTIVE_FOLLOW_UP_STATUSES),
                )
            )
            if earlier_pending is not None:
                counts["deferred"] += 1
                continue
            if not item.auto_send:
                item.status = FollowUpStatus.AWAITING_APPROVAL
                _audit(self.session, "follow_up.awaiting_approval", item, None, {})
                counts["awaiting_approval"] += 1
                continue
            if not self._sender_still_allowed(item):
                item.status = FollowUpStatus.AWAITING_APPROVAL
                _audit(
                    self.session,
                    "follow_up.awaiting_approval",
                    item,
                    None,
                    {"reason": "SCHEDULER_PERMISSION_REVOKED"},
                )
                counts["awaiting_approval"] += 1
                continue
            if not self.window.allows(self.now):
                item.due_at = self.window.next_opening(self.now)
                counts["deferred"] += 1
                continue
            if self._sent_today(item.organization_id) >= daily_limit():
                local = self.now.astimezone(self.window.timezone)
                tomorrow = datetime.combine(
                    local.date() + timedelta(days=1), day_time(0), tzinfo=self.window.timezone
                )
                item.due_at = self.window.next_opening(tomorrow)
                counts["deferred"] += 1
                continue
            _deliver(self.session, item, self.registry, self.now, self.unsubscribe_links)
            counts["sent" if item.status == FollowUpStatus.SENT else "failed"] += 1
        self.session.flush()
        return counts

    def _sender_still_allowed(self, item: OutboundFollowUp) -> bool:
        user = self.session.get(User, item.created_by_user_id)
        membership = self.session.scalar(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == item.organization_id,
                OrganizationMembership.user_id == item.created_by_user_id,
            )
        )
        return bool(
            user is not None
            and user.is_active
            and membership is not None
            and Permission.SEND_OUTBOUND_EMAIL in ROLE_PERMISSIONS[membership.role]
        )

    def _sent_today(self, organization_id: str) -> int:
        local = self.now.astimezone(self.window.timezone)
        start = datetime.combine(local.date(), day_time(0), tzinfo=self.window.timezone)
        return int(
            self.session.scalar(
                select(func.count(OutboundFollowUp.id)).where(
                    OutboundFollowUp.organization_id == organization_id,
                    OutboundFollowUp.status == FollowUpStatus.SENT,
                    OutboundFollowUp.sent_at >= start.astimezone(UTC),
                )
            )
            or 0
        )


# ---------------------------------------------------------------------- shared rules


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _conversation_stop_reason(session: Session, attempt_id: str) -> str | None:
    event = session.scalar(
        select(OutreachEngagementEvent.event_type)
        .where(OutreachEngagementEvent.send_attempt_id == attempt_id)
        .order_by(OutreachEngagementEvent.occurred_at.desc())
        .limit(1)
    )
    if event is None:
        return None
    value = getattr(event, "value", str(event))
    return "DELIVERED_ONLY" if value == "DELIVERED" else f"CONVERSATION_{value}"[:50]


def _stop_reason(session: Session, item: OutboundFollowUp) -> str | None:
    events = {
        getattr(value, "value", str(value))
        for value in session.scalars(
            select(OutreachEngagementEvent.event_type).where(
                OutreachEngagementEvent.send_attempt_id == item.initial_attempt_id
            )
        )
    }
    events.discard("DELIVERED")
    if events:
        for preferred in ("OPT_OUT", "COMPLAINT", "BOUNCED", "NEGATIVE_REPLY", "POSITIVE_REPLY"):
            if preferred in events:
                return f"CONVERSATION_{preferred}"
        return "CONVERSATION_REPLIED"
    email = session.get(ContactEmail, item.contact_email_id)
    if email is None or email.verification_status != EmailVerificationStatus.VALID:
        return "EMAIL_NOT_VERIFIED"
    suppressed = session.scalar(
        select(SuppressionEntry.id).where(
            SuppressionEntry.organization_id == item.organization_id,
            SuppressionEntry.email_normalized == email.email.strip().lower(),
        )
    )
    if suppressed is not None:
        return "RECIPIENT_SUPPRESSED"
    contact = session.get(ContactCandidate, item.contact_id)
    if contact is None:
        return "CONTACT_REMOVED"
    blocked = targeting_block_reason(contact)
    if blocked:
        return blocked[:50]
    return None


def _deliver(
    session: Session,
    item: OutboundFollowUp,
    registry: OutboundSenderRegistry,
    now: datetime,
    unsubscribe_links: UnsubscribeLinks | None,
) -> OutboundFollowUp:
    reason = _stop_reason(session, item)
    if reason:
        item.status = FollowUpStatus.SKIPPED
        item.stop_reason = reason
        _audit(session, "follow_up.skipped", item, item.decided_by_user_id, {"reason": reason})
        return item
    original = session.get(OutboundSendAttempt, item.initial_attempt_id)
    email = session.get(ContactEmail, item.contact_email_id)
    sender = registry.get(original.provider_key if original else "SMTP")
    item.provider_key = sender.key
    if not sender.configured:
        item.status = FollowUpStatus.FAILED
        item.stop_reason = "NOT_CONFIGURED"
        _audit(
            session, "follow_up.failed", item, item.decided_by_user_id, {"error": "NOT_CONFIGURED"}
        )
        return item
    references: tuple[str, ...] = ()
    if original is not None and original.provider_message_id:
        references = (original.provider_message_id,)
    earlier = session.scalars(
        select(OutboundFollowUp.provider_message_id).where(
            OutboundFollowUp.initial_attempt_id == item.initial_attempt_id,
            OutboundFollowUp.step < item.step,
            OutboundFollowUp.status == FollowUpStatus.SENT,
        )
    )
    references += tuple(value for value in earlier if value)
    try:
        result = sender.send(
            OutboundMessage(
                recipient=email.email,
                subject=item.subject,
                body=item.body,
                idempotency_key=f"follow-up-{item.id}",
                unsubscribe_url=(
                    unsubscribe_links.url(item.organization_id, email.email)
                    if unsubscribe_links is not None
                    else None
                ),
                in_reply_to=references[-1] if references else None,
                references=references,
            )
        )
        if not result.accepted:
            raise OutboundProviderError("Outbound provider rejected the message")
    except OutboundProviderError as error:
        item.status = FollowUpStatus.FAILED
        item.stop_reason = error.category[:50]
        _audit(
            session, "follow_up.failed", item, item.decided_by_user_id, {"error": item.stop_reason}
        )
        return item
    item.status = FollowUpStatus.SENT
    item.sent_at = now
    item.provider_message_id = result.provider_message_id
    _audit(
        session,
        "follow_up.sent",
        item,
        item.decided_by_user_id,
        {"auto": item.decided_by_user_id is None},
    )
    return item


def _audit(
    session: Session,
    action: str,
    item: OutboundFollowUp,
    actor_user_id: str | None,
    extra: dict,
) -> None:
    session.add(
        AuditLog(
            organization_id=item.organization_id,
            actor_user_id=actor_user_id,
            action=action,
            entity_type="outbound_follow_up",
            entity_id=item.id,
            metadata_json={
                "initial_attempt_id": item.initial_attempt_id,
                "contact_id": item.contact_id,
                "step": item.step,
                **extra,
            },
        )
    )
