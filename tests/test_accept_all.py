"""Accept-all domains: addresses Hunter scores 80+ can be drafted for and sent to."""

from __future__ import annotations

import pytest
from test_campaigns import seed_campaign, service_for
from test_follow_ups import ThreadSender
from test_outbound import approved_draft, outbound_service

from marketingiq.application.decision_maker_scoring import (
    EmailView,
    best_email,
    email_state,
)
from marketingiq.application.email_policy import is_sendable
from marketingiq.application.errors import ConflictError
from marketingiq.domain.models import (
    EmailVerificationStatus,
    MembershipRole,
    OrganizationMembership,
)

ACCEPT_ALL = EmailVerificationStatus.ACCEPT_ALL


@pytest.mark.parametrize(
    ("status", "score", "expected"),
    [
        (EmailVerificationStatus.VALID, None, True),
        (ACCEPT_ALL, 80, True),
        (ACCEPT_ALL, 79, False),
        (ACCEPT_ALL, None, False),
        (EmailVerificationStatus.RISKY, 99, False),
        (EmailVerificationStatus.UNKNOWN, 95, False),
    ],
)
def test_sendable_rule(status, score, expected):
    assert is_sendable(status, score) is expected


def test_scoring_prefers_valid_then_highest_accept_all_score():
    low = EmailView("a@x.test", ACCEPT_ALL, verification_score=78)
    high = EmailView("b@x.test", ACCEPT_ALL, verification_score=81)
    assert best_email([low, high]) is high
    assert email_state(high) == "ACCEPT_ALL"
    assert email_state(low) != "ACCEPT_ALL"
    valid = EmailView("c@x.test", EmailVerificationStatus.VALID)
    assert best_email([high, valid]) is valid


def test_draft_and_send_to_accept_all_80_but_not_79(session):
    seeded = seed_campaign(session)
    seeded["email"].verification_status = ACCEPT_ALL
    seeded["email"].verification_score = 79
    session.flush()
    service = service_for(session, seeded)
    ids = (seeded["relationship"].id, seeded["qualification"].id, seeded["contact"].id)
    with pytest.raises(ConflictError, match="VERIFIED_EMAIL_REQUIRED"):
        service.generate(*ids)

    seeded["email"].verification_score = 81
    session.add(
        OrganizationMembership(
            organization_id=seeded["organization"].id,
            user_id=seeded["user"].id,
            role=MembershipRole.MARKETING_USER,
        )
    )
    session.flush()
    draft, _approval = approved_draft(session, seeded)
    sender = ThreadSender()
    attempt = outbound_service(session, seeded, sender).send(
        seeded["relationship"].id, draft.id, provider_key="SMTP", idempotency_key="accept-all-01"
    )
    assert attempt.contact_email_id == seeded["email"].id
    assert sender.calls[-1].recipient == seeded["email"].email
