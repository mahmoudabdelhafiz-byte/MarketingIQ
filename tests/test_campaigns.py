from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from marketingiq.api.app import create_app
from marketingiq.application.auth import hash_password
from marketingiq.application.campaigns import CampaignDraftService
from marketingiq.application.errors import AuthorizationError, ConflictError, NotFoundError
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.campaigns import CampaignDraft
from marketingiq.domain.models import (
    ICP,
    AuditLog,
    Base,
    BuyerRoleMatch,
    Company,
    CompanyFact,
    ContactCandidate,
    ContactEmail,
    DataClassification,
    EmailVerificationStatus,
    FitGrade,
    FitStatus,
    LeadQualification,
    MembershipRole,
    Organization,
    OrganizationCompany,
    OrganizationMembership,
    Product,
    ProductFitAssessment,
    QualificationGrade,
    QualificationStatus,
    RedistributionStatus,
    User,
)

NOW = datetime(2026, 9, 15, 22, 0, tzinfo=UTC)
SECRET = "a-secure-campaign-api-secret-that-is-long-enough"
PASSWORD = "correct horse battery"


def seed_campaign(session: Session, *, role=MembershipRole.MARKETING_USER):
    suffix = uuid4().hex[:10]
    organization = Organization(name="Campaign Tenant", slug="campaign-" + suffix)
    user = User(email=f"campaign-{suffix}@example.test", password_hash="x")
    company = Company(canonical_name="Acme Logistics")
    product = Product(
        organization=organization,
        name="MarketingIQ",
        slug="marketingiq-" + suffix,
        value_proposition="MarketingIQ turns evidence into focused B2B pursuit decisions.",
        primary_buyer_roles=["VP Marketing"],
        secondary_buyer_roles=[],
    )
    relationship = OrganizationCompany(organization=organization, company=company)
    session.add_all([user, product, relationship])
    session.flush()

    icp = ICP(
        organization_id=organization.id,
        product=product,
        name="Core ICP",
        version=1,
        is_active=True,
    )
    session.add(icp)
    session.flush()
    icp.logical_id = icp.id

    fact = CompanyFact(
        company_id=company.id,
        fact_key="industry",
        value="Logistics",
        classification=DataClassification.PUBLIC_EVIDENCE,
        redistribution_status=RedistributionStatus.ALLOWED,
        confidence=90,
        observed_at=NOW - timedelta(days=1),
    )
    session.add(fact)
    session.flush()

    criterion = {
        "criterion_id": "industry",
        "type": "industry",
        "expected_value": "Logistics",
        "result": "MATCH",
        "required": True,
        "fact_key": "industry",
        "actual_value": "Logistics",
        "selected_fact_id": fact.id,
        "confidence": 90,
        "quality": "CURRENT",
    }
    assessment = ProductFitAssessment(
        organization_id=organization.id,
        company_id=company.id,
        organization_company_id=relationship.id,
        product_id=product.id,
        icp_id=icp.id,
        icp_version=1,
        score=90,
        evidence_coverage=90,
        confidence="HIGH",
        grade=FitGrade.A,
        status=FitStatus.COMPLETE,
        evaluated_at=NOW,
        workflow_version="test-fit",
        evidence_snapshot=[criterion],
        explanation={"criteria": [criterion]},
        classification=DataClassification.MARKETINGIQ_DERIVED,
        created_by_user_id=user.id,
    )
    session.add(assessment)
    session.flush()

    qualification = LeadQualification(
        organization_id=organization.id,
        organization_company_id=relationship.id,
        company_id=company.id,
        product_id=product.id,
        fit_assessment_id=assessment.id,
        qualification_score=90,
        qualification_grade=QualificationGrade.A,
        status=QualificationStatus.HIGH_PRIORITY,
        confidence="HIGH",
        recommended_buyer_role="VP Marketing",
        buyer_role_confidence="HIGH",
        alternative_buyer_roles=[],
        reasons={
            "positive_reasons": [
                {"code": "FIT_CRITERION_MATCH", "criterion_id": "industry"}
            ],
            "negative_reasons": [],
            "buyer_role_reason_codes": ["PRODUCT_PRIMARY_BUYER"],
            "fit_summary": {
                "fit_score": 90,
                "fit_grade": "A",
                "evidence_coverage": 90,
                "fit_status": "COMPLETE",
                "assessment_freshness": "CURRENT",
            },
        },
        research_gaps=[],
        component_scores={},
        workflow_version="test-qualification",
        qualified_at=NOW,
        created_by_user_id=user.id,
        classification=DataClassification.MARKETINGIQ_DERIVED,
    )
    session.add(qualification)
    session.flush()

    contact = ContactCandidate(
        organization_id=organization.id,
        organization_company_id=relationship.id,
        company_id=company.id,
        qualification_id=qualification.id,
        provider_key="HUNTER",
        provider_contact_reference="provider-person-secret",
        first_name="Ava",
        last_name="One",
        full_name="Ava One",
        job_title="VP Marketing",
        department="Marketing",
        seniority="VP",
        normalized_buyer_role="vp marketing",
        buyer_role_match=BuyerRoleMatch.EXACT,
        buyer_role_match_reason="Exact role match",
        confidence=95,
        discovered_at=NOW,
        classification=DataClassification.THIRD_PARTY_LICENSED,
        redistribution_status=RedistributionStatus.RESTRICTED,
    )
    session.add(contact)
    session.flush()
    email = ContactEmail(
        contact_id=contact.id,
        email="ava.one@example.test",
        source_provider="HUNTER",
        verification_status=EmailVerificationStatus.VALID,
        verification_score=97,
        found_at=NOW,
        verified_at=NOW,
        classification=DataClassification.THIRD_PARTY_LICENSED,
        redistribution_status=RedistributionStatus.RESTRICTED,
    )
    session.add(email)
    session.flush()

    return {
        "organization": organization,
        "user": user,
        "company": company,
        "product": product,
        "relationship": relationship,
        "assessment": assessment,
        "qualification": qualification,
        "contact": contact,
        "email": email,
        "role": role,
    }


def service_for(session: Session, seeded, role=None):
    return CampaignDraftService(
        session,
        TenantContext(
            seeded["organization"].id,
            seeded["user"].id,
            role or seeded["role"],
        ),
        NOW,
    )


def test_generate_is_grounded_append_only_and_safe(session):
    seeded = seed_campaign(session)
    service = service_for(session, seeded)

    first = service.generate(
        seeded["relationship"].id,
        seeded["qualification"].id,
        seeded["contact"].id,
    )
    second = service.generate(
        seeded["relationship"].id,
        seeded["qualification"].id,
        seeded["contact"].id,
        call_to_action="Would you be open to a short product-fit discussion next week?",
    )

    assert first.id != second.id
    assert first.subject == "MarketingIQ for Acme Logistics"
    assert "Hi Ava," in first.body
    assert "VP Marketing at Acme Logistics" in first.body
    assert "turns evidence into focused B2B pursuit decisions" in first.body
    assert first.redistribution_status == RedistributionStatus.INTERNAL_ONLY
    assert first.classification == DataClassification.MARKETINGIQ_DERIVED
    assert first.evidence_snapshot["criterion_ids"] == ["industry"]

    safe_snapshot = str(first.personalization_snapshot) + str(first.evidence_snapshot)
    assert seeded["email"].email not in safe_snapshot
    assert "provider-person-secret" not in safe_snapshot

    audits = list(
        session.scalars(
            select(AuditLog).where(AuditLog.action == "campaign_draft.generated")
        )
    )
    assert len(audits) == 2
    for audit in audits:
        metadata = str(audit.metadata_json)
        assert seeded["email"].email not in metadata
        assert "Ava One" not in metadata
        assert "provider-person-secret" not in metadata
        assert "turns evidence" not in metadata

    assert len(service.list(seeded["relationship"].id)) == 2
    assert service.get(seeded["relationship"].id, first.id).id == first.id


def test_generation_requires_actionable_current_qualification(session):
    seeded = seed_campaign(session)
    service = service_for(session, seeded)
    seeded["qualification"].status = QualificationStatus.NURTURE
    session.flush()

    with pytest.raises(ConflictError, match="QUALIFICATION_NOT_ACTIONABLE"):
        service.generate(
            seeded["relationship"].id,
            seeded["qualification"].id,
            seeded["contact"].id,
        )


def test_generation_rejects_stale_fit_inputs(session):
    seeded = seed_campaign(session)
    session.add(
        CompanyFact(
            company_id=seeded["company"].id,
            fact_key="industry",
            value="Financial Services",
            classification=DataClassification.PUBLIC_EVIDENCE,
            redistribution_status=RedistributionStatus.ALLOWED,
            confidence=99,
            observed_at=NOW,
        )
    )
    session.flush()

    with pytest.raises(ConflictError, match="REQUALIFICATION_REQUIRED"):
        service_for(session, seeded).generate(
            seeded["relationship"].id,
            seeded["qualification"].id,
            seeded["contact"].id,
        )


def test_generation_requires_role_alignment_verified_email_and_value_proposition(session):
    seeded = seed_campaign(session)
    service = service_for(session, seeded)

    seeded["contact"].buyer_role_match = BuyerRoleMatch.UNKNOWN
    session.flush()
    with pytest.raises(ConflictError, match="BUYER_ROLE_ALIGNMENT_REQUIRED"):
        service.generate(
            seeded["relationship"].id,
            seeded["qualification"].id,
            seeded["contact"].id,
        )

    seeded["contact"].buyer_role_match = BuyerRoleMatch.EXACT
    seeded["email"].verification_status = EmailVerificationStatus.RISKY
    session.flush()
    with pytest.raises(ConflictError, match="VERIFIED_EMAIL_REQUIRED"):
        service.generate(
            seeded["relationship"].id,
            seeded["qualification"].id,
            seeded["contact"].id,
        )

    seeded["email"].verification_status = EmailVerificationStatus.VALID
    seeded["product"].value_proposition = None
    session.flush()
    with pytest.raises(ConflictError, match="VALUE_PROPOSITION_REQUIRED"):
        service.generate(
            seeded["relationship"].id,
            seeded["qualification"].id,
            seeded["contact"].id,
        )


def test_read_only_can_read_but_cannot_generate(session):
    seeded = seed_campaign(session)
    admin = service_for(session, seeded, MembershipRole.ORGANIZATION_ADMIN)
    draft = admin.generate(
        seeded["relationship"].id,
        seeded["qualification"].id,
        seeded["contact"].id,
    )
    reader = service_for(session, seeded, MembershipRole.READ_ONLY)

    assert reader.get(seeded["relationship"].id, draft.id).id == draft.id
    with pytest.raises(AuthorizationError):
        reader.generate(
            seeded["relationship"].id,
            seeded["qualification"].id,
            seeded["contact"].id,
        )


def test_campaign_drafts_are_tenant_isolated(session):
    first = seed_campaign(session)
    second = seed_campaign(session)
    draft = service_for(session, first).generate(
        first["relationship"].id,
        first["qualification"].id,
        first["contact"].id,
    )

    with pytest.raises(NotFoundError):
        service_for(session, second).get(first["relationship"].id, draft.id)


def seed_api_database(url: str):
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    suffix = uuid4().hex[:10]
    with Session(engine) as session:
        seeded = seed_campaign(session)
        marketing = seeded["user"]
        marketing.password_hash = hash_password(PASSWORD)
        reader = User(
            email=f"campaign-reader-{suffix}@example.test",
            password_hash=hash_password(PASSWORD),
        )
        session.add(reader)
        session.flush()
        session.add_all(
            [
                OrganizationMembership(
                    organization_id=seeded["organization"].id,
                    user_id=marketing.id,
                    role=MembershipRole.MARKETING_USER,
                ),
                OrganizationMembership(
                    organization_id=seeded["organization"].id,
                    user_id=reader.id,
                    role=MembershipRole.READ_ONLY,
                ),
            ]
        )
        session.commit()
        return {
            "org_id": seeded["organization"].id,
            "relationship_id": seeded["relationship"].id,
            "qualification_id": seeded["qualification"].id,
            "contact_id": seeded["contact"].id,
            "marketing_email": marketing.email,
            "reader_email": reader.email,
        }


def login(client: TestClient, email: str):
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": PASSWORD},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_campaign_api_generation_listing_and_rbac(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'campaign-api.db'}"
    seeded = seed_api_database(url)
    client = TestClient(create_app(url, SECRET))
    marketing = login(client, seeded["marketing_email"])
    reader = login(client, seeded["reader_email"])
    base = (
        f"/api/v1/organizations/{seeded['org_id']}/companies/"
        f"{seeded['relationship_id']}/campaign-drafts"
    )
    payload = {
        "qualification_id": seeded["qualification_id"],
        "contact_id": seeded["contact_id"],
    }

    denied = client.post(base, headers=reader, json=payload)
    assert denied.status_code == 403

    created = client.post(base, headers=marketing, json=payload)
    assert created.status_code == 201
    data = created.json()
    assert data["status"] == "DRAFT"
    assert data["channel"] == "EMAIL"
    assert "email" not in data["personalization_snapshot"]
    assert "provider_contact_reference" not in data["personalization_snapshot"]

    listing = client.get(base, headers=reader)
    detail = client.get(f"{base}/{data['id']}", headers=reader)
    assert listing.status_code == 200
    assert len(listing.json()) == 1
    assert detail.status_code == 200
    assert detail.json()["id"] == data["id"]

    engine = create_engine(url)
    with Session(engine) as session:
        assert session.scalar(select(CampaignDraft)) is not None
