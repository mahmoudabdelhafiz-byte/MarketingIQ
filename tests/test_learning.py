from __future__ import annotations

from fastapi.testclient import TestClient
from test_campaigns import SECRET, login, seed_api_database, seed_campaign
from test_outbound import FakeSender, approved_draft, outbound_service

from marketingiq.api.app import create_app
from marketingiq.application.learning import ConversionIntelligenceService
from marketingiq.application.outbound import OutboundSenderRegistry
from marketingiq.application.pipeline import SalesPipelineService
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.models import (
    CompanyFact,
    DataClassification,
    MembershipRole,
    RedistributionStatus,
)
from marketingiq.domain.pipeline import OpportunityStage


def services(session, seeded):
    tenant = TenantContext(
        seeded["organization"].id,
        seeded["user"].id,
        MembershipRole.MARKETING_USER,
    )
    return SalesPipelineService(session, tenant), ConversionIntelligenceService(session, tenant)


def sent_attempt(session, seeded):
    draft, _ = approved_draft(session, seeded)
    sender = FakeSender()
    attempt = outbound_service(session, seeded, sender).send(
        seeded["relationship"].id,
        draft.id,
        provider_key="SMTP",
        idempotency_key="learning-send-001",
    )
    return draft, attempt


def test_conversion_summary_uses_observed_stage_history(session):
    seeded = seed_campaign(session)
    _draft, attempt = sent_attempt(session, seeded)
    pipeline, learning = services(session, seeded)
    opportunity = pipeline.create(seeded["relationship"].id, attempt.id)
    pipeline.move_stage(
        seeded["relationship"].id,
        opportunity.id,
        OpportunityStage.RESPONDED,
    )
    pipeline.move_stage(
        seeded["relationship"].id,
        opportunity.id,
        OpportunityStage.MEETING,
    )
    pipeline.move_stage(
        seeded["relationship"].id,
        opportunity.id,
        OpportunityStage.LOST,
        reason_code="NO_BUDGET",
    )

    summary = learning.summary(seeded["product"].id)

    assert summary["sample_size"] == 1
    assert summary["counts"] == {
        "contacted": 1,
        "responded": 1,
        "meeting": 1,
        "proposal": 0,
        "won": 0,
        "lost": 1,
    }
    assert summary["rates"]["response_rate"] == 100.0
    assert summary["rates"]["meeting_rate"] == 100.0
    assert summary["rates"]["win_rate"] == 0.0
    assert summary["methodology"]["predictive"] is False
    assert summary["methodology"]["causal"] is False


def test_grouped_learning_uses_safe_aggregate_dimensions(session):
    seeded = seed_campaign(session)
    draft, attempt = sent_attempt(session, seeded)
    pipeline, learning = services(session, seeded)
    opportunity = pipeline.create(seeded["relationship"].id, attempt.id)
    pipeline.move_stage(
        seeded["relationship"].id,
        opportunity.id,
        OpportunityStage.RESPONDED,
    )

    roles = learning.buyer_role_performance(seeded["product"].id)
    angles = learning.message_angle_performance(seeded["product"].id)
    grades = learning.qualification_grade_performance(seeded["product"].id)
    industries = learning.industry_performance(seeded["product"].id)
    countries = learning.country_performance(seeded["product"].id)

    assert len(roles) == 1
    assert roles[0]["sample_size"] == 1
    assert roles[0]["value"]
    assert roles[0]["rates"]["response_rate"] == 100.0
    assert len(angles) == 1
    assert angles[0]["value"] == draft.message_angle
    assert grades[0]["value"] == "A"
    assert grades[0]["dimension_source"] == "IMMUTABLE_LEAD_QUALIFICATION"
    assert industries[0]["value"] == "Logistics"
    assert industries[0]["dimension_source"] == "IMMUTABLE_FIT_EVIDENCE_SNAPSHOT"
    assert countries[0]["value"] == "UNKNOWN"
    assert "email" not in str(roles).lower()
    assert "email" not in str(industries).lower()


def test_snapshot_dimension_does_not_follow_later_current_fact_change(session):
    seeded = seed_campaign(session)
    _draft, attempt = sent_attempt(session, seeded)
    pipeline, learning = services(session, seeded)
    pipeline.create(seeded["relationship"].id, attempt.id)

    session.add(
        CompanyFact(
            company_id=seeded["company"].id,
            fact_key="industry",
            value="Manufacturing",
            classification=DataClassification.PUBLIC_EVIDENCE,
            redistribution_status=RedistributionStatus.ALLOWED,
            confidence=99,
            observed_at=seeded["assessment"].evaluated_at,
        )
    )
    session.flush()

    industries = learning.industry_performance(seeded["product"].id)

    assert industries[0]["value"] == "Logistics"
    assert "Manufacturing" not in str(industries)


def test_minimum_sample_size_suppresses_small_groups(session):
    seeded = seed_campaign(session)
    _draft, attempt = sent_attempt(session, seeded)
    pipeline, learning = services(session, seeded)
    pipeline.create(seeded["relationship"].id, attempt.id)

    assert learning.buyer_role_performance(
        seeded["product"].id,
        min_sample_size=2,
    ) == []
    assert learning.industry_performance(
        seeded["product"].id,
        min_sample_size=2,
    ) == []


def test_learning_api_is_readable_by_read_only_role(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'learning-api.db'}"
    seeded = seed_api_database(url)
    app = create_app(url, SECRET)
    app.state.outbound_sender_registry = OutboundSenderRegistry([FakeSender()])
    client = TestClient(app)
    marketing = login(client, seeded["marketing_email"])
    reader = login(client, seeded["reader_email"])

    campaign_base = (
        f"/api/v1/organizations/{seeded['org_id']}/companies/"
        f"{seeded['relationship_id']}/campaign-drafts"
    )
    generated = client.post(
        campaign_base,
        headers=marketing,
        json={
            "qualification_id": seeded["qualification_id"],
            "contact_id": seeded["contact_id"],
        },
    )
    assert generated.status_code == 201
    draft_id = generated.json()["id"]
    assert client.post(
        f"{campaign_base}/{draft_id}/approve",
        headers=marketing,
        json={"reason": "Reviewed"},
    ).status_code == 200
    sent = client.post(
        f"{campaign_base}/{draft_id}/send",
        headers=marketing,
        json={"provider": "SMTP", "idempotency_key": "learning-api-send-001"},
    )
    assert sent.status_code == 201

    opportunity_base = (
        f"/api/v1/organizations/{seeded['org_id']}/companies/"
        f"{seeded['relationship_id']}/opportunities"
    )
    assert client.post(
        opportunity_base,
        headers=marketing,
        json={"send_attempt_id": sent.json()["id"]},
    ).status_code == 201

    learning_base = f"/api/v1/organizations/{seeded['org_id']}/learning"
    summary = client.get(
        learning_base + "/conversion-summary",
        headers=reader,
    )
    assert summary.status_code == 200
    assert summary.json()["sample_size"] == 1

    roles = client.get(
        learning_base + "/buyer-role-performance",
        headers=reader,
    )
    assert roles.status_code == 200
    assert roles.json()[0]["sample_size"] == 1

    industries = client.get(
        learning_base + "/industry-performance",
        headers=reader,
    )
    assert industries.status_code == 200
    assert industries.json()[0]["value"] == "Logistics"

    grades = client.get(
        learning_base + "/qualification-grade-performance",
        headers=reader,
    )
    assert grades.status_code == 200
    assert grades.json()[0]["value"] == "A"
