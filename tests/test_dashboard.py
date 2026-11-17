"""Dashboard numbers: weekly activity, funnel, pipeline, intents and recent activity."""

from datetime import timedelta

from test_replies import NOW as REPLY_NOW
from test_replies import assistant, captured

from marketingiq.application.dashboard import DashboardService
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.models import MembershipRole


def service(session, seeded, role=MembershipRole.MARKETING_USER):
    return DashboardService(
        session,
        TenantContext(seeded["organization"].id, seeded["user"].id, role),
        now=REPLY_NOW + timedelta(hours=1),
    )


def test_dashboard_counts_activity_and_pipeline(session):
    sender, seeded, attempt, reply = captured(session)
    assistant(session, seeded, sender).mark_meeting_booked(reply.id)

    data = service(session, seeded).build(90)

    assert data["period"]["days"] == 90
    assert data["kpis"]["emails_sent"]["value"] == 1
    assert data["kpis"]["reply_rate"]["value"] == 1.0
    assert data["kpis"]["meetings"]["value"] == 1
    assert data["kpis"]["open_opportunities"]["value"] == 1
    weekly = data["weekly"]
    assert sum(w["emails_sent"] for w in weekly) == 1
    assert sum(w["replies"] for w in weekly) == 1
    assert sum(w["meetings"] for w in weekly) == 1
    assert [w["week_start"] for w in weekly] == sorted(w["week_start"] for w in weekly)
    funnel = {f["key"]: f["value"] for f in data["funnel"]}
    assert funnel["emails_sent"] == 1 and funnel["meetings"] == 1
    assert {p["stage"]: p["count"] for p in data["pipeline"]}["MEETING"] == 1
    assert data["reply_intents"] == [{"intent": "MEETING_REQUEST", "count": 1}]
    kinds = [item["kind"] for item in data["recent"]]
    assert kinds[0] in {"MEETING", "REPLY"} and "EMAIL_SENT" in kinds
    assert all(item["company"] for item in data["recent"])


def test_period_snaps_to_supported_values(session):
    _sender, seeded, _attempt, _reply = captured(session)
    assert service(session, seeded).build(100)["period"]["days"] == 90
    assert service(session, seeded).build(7)["period"]["days"] == 30


def test_other_organisation_sees_nothing(session):
    from test_campaigns import seed_campaign

    _sender, _seeded, _attempt, _reply = captured(session)
    other = seed_campaign(session)
    data = service(session, other).build(90)
    assert data["kpis"]["emails_sent"]["value"] == 0
    assert data["recent"] == []

