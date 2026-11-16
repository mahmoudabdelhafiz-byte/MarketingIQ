"""Stop pending follow-ups as soon as the conversation ends, not only when they come due.

The follow-up job re-checks every stop rule before sending anyway; this keeps the Follow-ups page
truthful in the meantime (a follow-up after a reply must not look scheduled). It depends only on
domain models so engagement, outbound and unsubscribe code can call it without import cycles.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketingiq.domain.follow_ups import (
    ACTIVE_FOLLOW_UP_STATUSES,
    FollowUpStatus,
    OutboundFollowUp,
)
from marketingiq.domain.models import AuditLog, ContactEmail


def stop_pending_follow_ups(
    session: Session,
    reason: str,
    *,
    attempt_id: str | None = None,
    organization_id: str | None = None,
    email: str | None = None,
    actor_user_id: str | None = None,
    now: datetime | None = None,
) -> int:
    query = select(OutboundFollowUp).where(OutboundFollowUp.status.in_(ACTIVE_FOLLOW_UP_STATUSES))
    if attempt_id is not None:
        query = query.where(OutboundFollowUp.initial_attempt_id == attempt_id)
    elif organization_id is not None and email:
        query = query.join(
            ContactEmail, ContactEmail.id == OutboundFollowUp.contact_email_id
        ).where(
            OutboundFollowUp.organization_id == organization_id,
            ContactEmail.email == email.strip().lower(),
        )
    else:
        return 0
    when = now or datetime.now(UTC)
    items = list(session.scalars(query))
    for item in items:
        item.status = FollowUpStatus.SKIPPED
        item.stop_reason = reason[:50]
        item.updated_at = when
        session.add(
            AuditLog(
                organization_id=item.organization_id,
                actor_user_id=actor_user_id,
                action="follow_up.stopped",
                entity_type="outbound_follow_up",
                entity_id=item.id,
                metadata_json={"step": item.step, "reason": item.stop_reason},
            )
        )
    if items:
        session.flush()
    return len(items)
