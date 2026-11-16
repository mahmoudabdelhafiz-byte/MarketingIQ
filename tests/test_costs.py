"""Cost dashboard: usage × prices, funnel counts and unit costs."""

from __future__ import annotations

from datetime import timedelta

import pytest
from test_campaigns import seed_campaign
from test_follow_ups import ThreadSender, sent_email
from test_replies import NOW as REPLY_NOW
from test_replies import assistant, ingest, reply_raw

from marketingiq.application.costs import CostReportService
from marketingiq.application.errors import AuthorizationError
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.models import AuditLog, MembershipRole, ProviderUsage


def costs(session, seeded, role=MembershipRole.MARKETING_USER, now=None):
    return CostReportService(
        session,
        TenantContext(seeded["organization"].id, seeded["user"].id, role),
        now=now or REPLY_NOW + timedelta(hours=1),
    )


def test_report_prices_usage_and_unit_costs(session):
    sender = ThreadSender()
    seeded, _draft, attempt = sent_email(session, sender)
    org = seeded["organization"].id
    when = REPLY_NOW - timedelta(days=1)
    session.add_all(
        [
            ProviderUsage(
                provider_key="HUNTER",
                organization_id=org,
                company_id=seeded["company"].id,
                operation="email_finder",
                requested_at=when,
                success=True,
                credits_used=10,
            ),
            ProviderUsage(  # cached lookups cost nothing
                provider_key="HUNTER",
                organization_id=org,
                company_id=seeded["company"].id,
                operation="email_finder",
                requested_at=when,
                success=True,
                credits_used=5,
                cache_hit=True,
            ),
            AuditLog(
                organization_id=org,
                action="company_discovery.ai_search",
                entity_type="company_discovery",
                entity_id="x",
                metadata_json={
                    "context": "preview",
                    "searches": 4,
                    "input_tokens": 1_500_000,
                    "output_tokens": 500_000,
                },
                created_at=when,
            ),
        ]
    )
    session.flush()
    ingest(session, reply_raw(attempt.provider_message_id, "Yes, a call next week works."))
    from sqlalchemy import select

    from marketingiq.domain.replies import InboundReply

    reply = session.scalar(select(InboundReply))
    assistant(session, seeded, sender).mark_meeting_booked(reply.id)

    svc = costs(session, seeded)
    svc.update_settings(
        currency="sar",
        provider_credit_cost=2,
        ai_input_per_million=10,
        ai_output_per_million=40,
        web_search_cost=0.5,
        monthly_fixed_cost=300,
    )
    report = svc.report(90)  # the email went out on 15 Sep, the reply came on 16 Nov

    assert report["currency"] == "SAR" and report["prices_missing"] == []
    assert report["usage"]["provider_credits"] == 10
    assert report["usage"]["provider_credits_estimated"] == 0
    assert report["usage"]["ai_web_searches"] == 4
    costs_by_label = {line["label"]: line["cost"] for line in report["cost_lines"]}
    assert costs_by_label["Provider credits (Hunter)"] == 20.0
    assert costs_by_label["AI input tokens"] == 15.0
    assert costs_by_label["AI output tokens"] == 20.0
    assert costs_by_label["AI web searches"] == 2.0
    assert costs_by_label["Subscriptions and fixed costs (pro rata)"] == 900.0
    assert report["total_cost"] == 957.0

    funnel = report["funnel"]
    assert funnel["emails_sent"] == 1 and funnel["replies"] == 1 and funnel["meetings"] == 1
    assert report["unit_costs"]["per_meeting"] == 957.0
    assert report["unit_costs"]["per_reply"] == 957.0
    product = report["per_product"][0]
    assert (product["emails_sent"], product["replies"], product["meetings"]) == (1, 1, 1)
    assert product["reply_rate"] == 1.0


def test_missing_prices_are_listed_and_cost_zero(session):
    sender = ThreadSender()
    seeded, _draft, _attempt = sent_email(session, sender)
    report = costs(session, seeded).report(90)
    assert report["total_cost"] == 0.0
    assert "Subscriptions and fixed costs (pro rata)" in report["prices_missing"]
    assert report["unit_costs"]["per_email_sent"] == 0.0
    assert report["unit_costs"]["per_meeting"] is None


def test_settings_validation_permissions_and_isolation(session):
    seeded = seed_campaign(session)
    svc = costs(session, seeded)
    with pytest.raises(ValueError):
        svc.update_settings(currency="US")
    with pytest.raises(ValueError):
        svc.update_settings(provider_credit_cost=-1)
    with pytest.raises(AuthorizationError):
        costs(session, seeded, role=MembershipRole.READ_ONLY).update_settings(currency="USD")
    svc.update_settings(currency="EGP", monthly_fixed_cost=100)
    other = seed_campaign(session)
    assert costs(session, other).describe_settings()["currency"] == "USD"
    assert costs(session, seeded).describe_settings()["monthly_fixed_cost"] == 100.0


def test_unreported_hunter_credits_are_estimated_per_request(session):
    sender = ThreadSender()
    seeded, _draft, _attempt = sent_email(session, sender)
    when = REPLY_NOW - timedelta(days=1)
    for success in (True, True, False):
        session.add(
            ProviderUsage(
                provider_key="HUNTER",
                organization_id=seeded["organization"].id,
                company_id=seeded["company"].id,
                operation="EMAIL_VERIFY",
                requested_at=when,
                success=success,
                credits_used=None,
            )
        )
    session.flush()
    report = costs(session, seeded).report(30)
    assert report["usage"]["provider_credits"] == 2  # failed requests are not charged
    assert report["cost_lines"][0]["estimated_quantity"] == 2
