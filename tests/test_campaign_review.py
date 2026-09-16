from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from test_campaigns import (
    NOW,
    SECRET,
    login,
    seed_api_database,
    seed_campaign,
    service_for,
)

from marketingiq.api.app import create_app
from marketingiq.application.errors import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
)
from marketingiq.domain.campaigns import (
    CampaignDraftReviewEvent,
    CampaignDraftStatus,
    CampaignReviewAction,
)
from marketingiq.domain.models import (
    AuditLog,
    CompanyFact,
    DataClassification,
    MembershipRole,
    RedistributionStatus,
)


def _generate(session, seeded):
    return service_for(session, seeded).generate(
        seeded["relationship"].id,
        seeded["qualification"].id,
        seeded["contact"].id,
    )


def test_edit_is_append_only_and_effective_content_changes(session):
    seeded = seed_campaign(session)
    service = service_for(session, seeded)
    draft = _generate(session, seeded)
    original = (draft.subject, draft.body, draft.call_to_action)

    event = service.edit(
        seeded["relationship"].id,
        draft.id,
        subject="A focused MarketingIQ discussion",
        body="Updated tenant-private review copy",
        reason="Tighten the message",
    )

    assert event.action == CampaignReviewAction.EDIT
    assert event.status == CampaignDraftStatus.DRAFT
    assert event.revision_number == 1
    assert (draft.subject, draft.body, draft.call_to_action) == original

    state = service.effective_state(seeded["relationship"].id, draft.id)
    assert state["subject"] == "A focused MarketingIQ discussion"
    assert state["body"] == "Updated tenant-private review copy"
    assert state["call_to_action"] == original[2]
    assert state["revision_number"] == 1

    history = service.history(seeded["relationship"].id, draft.id)
    assert [item.revision_number for item in history] == [1]

    audit = session.scalar(
        select(AuditLog).where(AuditLog.action == "campaign_draft.edited")
    )
    metadata = str(audit.metadata_json)
    assert "Updated tenant-private review copy" not in metadata
    assert seeded["email"].email not in metadata


def test_reject_edit_approve_preserves_full_history(session):
    seeded = seed_campaign(session)
    service = service_for(session, seeded)
    draft = _generate(session, seeded)

    rejected = service.reject(
        seeded["relationship"].id,
        draft.id,
        "Needs a more concise subject",
    )
    assert rejected.status == CampaignDraftStatus.REJECTED

    with pytest.raises(ConflictError, match="EDIT_REQUIRED_AFTER_REJECTION"):
        service.approve(seeded["relationship"].id, draft.id)

    edited = service.edit(
        seeded["relationship"].id,
        draft.id,
        subject="MarketingIQ discussion",
    )
    approved = service.approve(
        seeded["relationship"].id,
        draft.id,
        "Reviewed and ready for later sending workflow",
    )

    assert edited.revision_number == 2
    assert approved.revision_number == 3
    assert approved.status == CampaignDraftStatus.APPROVED
    assert approved.subject == "MarketingIQ discussion"

    history = service.history(seeded["relationship"].id, draft.id)
    assert [item.action for item in history] == [
        CampaignReviewAction.REJECT,
        CampaignReviewAction.EDIT,
        CampaignReviewAction.APPROVE,
    ]
    assert [item.revision_number for item in history] == [1, 2, 3]

    with pytest.raises(ConflictError, match="ALREADY_APPROVED"):
        service.edit(seeded["relationship"].id, draft.id, subject="Too late")
    with pytest.raises(ConflictError, match="ALREADY_APPROVED"):
        service.reject(seeded["relationship"].id, draft.id, "Too late")


def test_approval_rechecks_fit_freshness(session):
    seeded = seed_campaign(session)
    service = service_for(session, seeded)
    draft = _generate(session, seeded)
    service.edit(seeded["relationship"].id, draft.id, subject="Reviewed subject")

    session.add(
        CompanyFact(
            company_id=seeded["company"].id,
            fact_key="industry",
            value="Financial Services",
            classification=DataClassification.PUBLIC_EVIDENCE,
            redistribution_status=RedistributionStatus.ALLOWED,
            confidence=99,
            observed_at=NOW + timedelta(minutes=1),
        )
    )
    session.flush()

    with pytest.raises(ConflictError, match="REQUALIFICATION_REQUIRED"):
        service.approve(seeded["relationship"].id, draft.id)

    state = service.effective_state(seeded["relationship"].id, draft.id)
    assert state["status"] == CampaignDraftStatus.DRAFT
    assert len(service.history(seeded["relationship"].id, draft.id)) == 1


def test_read_only_can_view_history_but_cannot_review(session):
    seeded = seed_campaign(session)
    marketer = service_for(session, seeded)
    draft = _generate(session, seeded)
    marketer.edit(seeded["relationship"].id, draft.id, subject="Reviewed")

    reader = service_for(session, seeded, MembershipRole.READ_ONLY)
    assert len(reader.history(seeded["relationship"].id, draft.id)) == 1
    assert reader.effective_state(seeded["relationship"].id, draft.id)["subject"] == "Reviewed"

    with pytest.raises(AuthorizationError):
        reader.edit(seeded["relationship"].id, draft.id, subject="Forbidden")
    with pytest.raises(AuthorizationError):
        reader.approve(seeded["relationship"].id, draft.id)
    with pytest.raises(AuthorizationError):
        reader.reject(seeded["relationship"].id, draft.id, "Forbidden")


def test_review_history_is_tenant_isolated(session):
    first = seed_campaign(session)
    second = seed_campaign(session)
    draft = _generate(session, first)
    service_for(session, first).edit(first["relationship"].id, draft.id, subject="Private")

    with pytest.raises(NotFoundError):
        service_for(session, second).history(first["relationship"].id, draft.id)


def test_review_events_and_audits_do_not_mutate_generated_draft(session):
    seeded = seed_campaign(session)
    service = service_for(session, seeded)
    draft = _generate(session, seeded)
    generated_subject = draft.subject

    service.edit(
        seeded["relationship"].id,
        draft.id,
        subject="Human revision secret copy",
    )
    service.approve(seeded["relationship"].id, draft.id)

    session.refresh(draft)
    assert draft.subject == generated_subject
    assert draft.status == CampaignDraftStatus.DRAFT
    assert len(list(session.scalars(select(CampaignDraftReviewEvent)))) == 2

    for audit in session.scalars(
        select(AuditLog).where(
            AuditLog.action.in_(["campaign_draft.edited", "campaign_draft.approved"])
        )
    ):
        metadata = str(audit.metadata_json)
        assert "Human revision secret copy" not in metadata
        assert seeded["email"].email not in metadata


def test_campaign_review_api_edit_approve_history_and_rbac(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'campaign-review-api.db'}"
    seeded = seed_api_database(url)
    client = TestClient(create_app(url, SECRET))
    marketing = login(client, seeded["marketing_email"])
    reader = login(client, seeded["reader_email"])
    base = (
        f"/api/v1/organizations/{seeded['org_id']}/companies/"
        f"{seeded['relationship_id']}/campaign-drafts"
    )
    generated = client.post(
        base,
        headers=marketing,
        json={
            "qualification_id": seeded["qualification_id"],
            "contact_id": seeded["contact_id"],
        },
    )
    assert generated.status_code == 201
    draft_id = generated.json()["id"]

    denied = client.patch(
        f"{base}/{draft_id}", headers=reader, json={"subject": "Forbidden"}
    )
    assert denied.status_code == 403

    edited = client.patch(
        f"{base}/{draft_id}",
        headers=marketing,
        json={"subject": "Human reviewed subject", "reason": "Make it clearer"},
    )
    assert edited.status_code == 200
    assert edited.json()["subject"] == "Human reviewed subject"
    assert edited.json()["generated_content"]["subject"] != "Human reviewed subject"
    assert edited.json()["revision_number"] == 1

    approved = client.post(
        f"{base}/{draft_id}/approve",
        headers=marketing,
        json={"reason": "Approved after review"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "APPROVED"
    assert approved.json()["revision_number"] == 2

    history = client.get(f"{base}/{draft_id}/history", headers=reader)
    assert history.status_code == 200
    assert [item["action"] for item in history.json()] == ["EDIT", "APPROVE"]

    engine = create_engine(url)
    with Session(engine) as session:
        events = list(session.scalars(select(CampaignDraftReviewEvent)))
        assert len(events) == 2
