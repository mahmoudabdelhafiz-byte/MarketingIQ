"""Which found emails may be drafted for and sent to.

A VALID mailbox is always sendable. Many company domains (common in the Gulf) accept every
address, so Hunter can only answer ACCEPT_ALL; those are sendable when Hunter's score for that
exact address is at least ``ACCEPT_ALL_MIN_SCORE``. Everything else stays blocked.
"""

from __future__ import annotations

from sqlalchemy import and_, case, or_

from marketingiq.domain.models import ContactEmail, EmailVerificationStatus

ACCEPT_ALL_MIN_SCORE = 80


def is_sendable(status, score: int | None) -> bool:
    status = EmailVerificationStatus(status) if status else None
    if status == EmailVerificationStatus.VALID:
        return True
    return status == EmailVerificationStatus.ACCEPT_ALL and (score or 0) >= ACCEPT_ALL_MIN_SCORE


def sendable_condition():
    """SQL condition matching ``is_sendable`` for ContactEmail rows."""
    return or_(
        ContactEmail.verification_status == EmailVerificationStatus.VALID,
        and_(
            ContactEmail.verification_status == EmailVerificationStatus.ACCEPT_ALL,
            ContactEmail.verification_score >= ACCEPT_ALL_MIN_SCORE,
        ),
    )


def sendable_order():
    """Prefer fully verified mailboxes over accept-all ones."""
    return case((ContactEmail.verification_status == EmailVerificationStatus.VALID, 0), else_=1)
