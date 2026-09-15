from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select

from marketingiq.application.errors import AuthorizationError, NotFoundError
from marketingiq.application.qualification import LeadQualificationService
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.models import (
    ICP,
    AuditLog,
    Company,
    CompanyFact,
    DataClassification,
    FitGrade,
    FitStatus,
    MembershipRole,
    Organization,
    OrganizationCompany,
    Product,
    ProductFitAssessment,
    QualificationGrade,
    QualificationStatus,
    RedistributionStatus,
    User,
)

NOW = datetime(2026, 9, 15, tzinfo=UTC)


def setup(
    session,
    *,
    score=90,
    coverage=90,
    confidence="HIGH",
    status=FitStatus.COMPLETE,
    criteria=None,
    roles=None,
    secondary=None,
    role=MembershipRole.MARKETING_USER,
):
    suffix = uuid4().hex[:10]
    org = Organization(name="Tenant", slug="qualification-" + suffix)
    user = User(email="qualification-" + suffix + "@test", password_hash="x")
    company = Company(canonical_name="Target")
    product = Product(
        organization=org,
        name="Product",
        slug="product-" + suffix,
        primary_buyer_roles=roles or [],
        secondary_buyer_roles=secondary or [],
    )
    icp = ICP(organization_id=org.id, product=product, name="ICP", version=1, is_active=True)
    relationship = OrganizationCompany(organization=org, company=company)
    session.add_all([user, product, relationship])
    session.flush()
    icp.logical_id = icp.id
    criteria = (
        criteria
        if criteria is not None
        else [
            {
                "criterion_id": "industry",
                "type": "industry",
                "expected_value": "Software",
                "result": "MATCH",
                "required": True,
                "fact_key": "industry",
                "actual_value": "Software",
            }
        ]
    )
    for criterion in criteria:
        if criterion.get("result") == "UNKNOWN" or not criterion.get("fact_key"):
            continue
        fact = CompanyFact(
            company_id=company.id,
            fact_key=criterion["fact_key"],
            value=criterion.get("actual_value"),
            classification=DataClassification.PUBLIC_EVIDENCE,
            redistribution_status=RedistributionStatus.ALLOWED,
            confidence=90,
            observed_at=NOW,
        )
        session.add(fact)
        session.flush()
        criterion["selected_fact_id"] = fact.id
        criterion["confidence"] = 90
        criterion["quality"] = "CURRENT"
    assessment = ProductFitAssessment(
        organization_id=org.id,
        company_id=company.id,
        organization_company_id=relationship.id,
        product_id=product.id,
        icp_id=icp.id,
        icp_version=1,
        score=score,
        evidence_coverage=coverage,
        confidence=confidence,
        grade=FitGrade.A if score >= 80 else FitGrade.C if score >= 40 else FitGrade.D,
        status=status,
        evaluated_at=NOW,
        workflow_version="test",
        evidence_snapshot=criteria,
        explanation={"criteria": criteria},
        classification=DataClassification.MARKETINGIQ_DERIVED,
        created_by_user_id=user.id,
    )
    session.add(assessment)
    session.flush()
    service = LeadQualificationService(session, TenantContext(org.id, user.id, role), NOW)
    return service, relationship, product, icp, assessment


def test_strong_fit_coverage_roles_repeatability_history_and_safe_audit(session):
    svc, rel, _product, _icp, assessment = setup(
        session, roles=["CIO / CTO", "IT Director"], secondary=["Procurement"]
    )
    first = svc.execute(rel.id, assessment.id)
    second = svc.execute(rel.id, assessment.id)
    assert first.status == QualificationStatus.HIGH_PRIORITY
    assert first.qualification_score == 92
    assert first.qualification_grade == QualificationGrade.A
    assert first.recommended_buyer_role == "CIO / CTO"
    assert first.alternative_buyer_roles == ["IT Director", "Procurement"]
    assert first.component_scores == second.component_scores
    assert first.id != second.id and len(svc.list(rel.id)) == 2
    assert first.classification == DataClassification.MARKETINGIQ_DERIVED
    audit = session.scalar(select(AuditLog).where(AuditLog.entity_id == first.id))
    assert set(audit.metadata_json) == {
        "company_id",
        "product_id",
        "fit_assessment_id",
        "qualification_id",
        "qualification_score",
        "status",
        "recommended_buyer_role",
    }
    serialized = str(first.reasons) + str(first.research_gaps)
    assert "reference_url" not in serialized and "provider_payload" not in serialized.casefold()


def test_high_fit_low_coverage_and_unknown_are_research_not_no_match(session):
    unknown = [
        {
            "criterion_id": "country",
            "type": "country",
            "result": "UNKNOWN",
            "required": True,
            "fact_key": "country",
            "actual_value": None,
        }
    ]
    svc, rel, _product, _icp, assessment = setup(
        session,
        score=100,
        coverage=20,
        confidence="LOW",
        status=FitStatus.NEEDS_MORE_RESEARCH,
        criteria=unknown,
    )
    result = svc.execute(rel.id, assessment.id)
    assert result.status == QualificationStatus.NEEDS_MORE_RESEARCH
    assert result.status != QualificationStatus.NOT_QUALIFIED
    assert result.research_gaps[0]["code"] == "REQUIRED_ICP_CRITERION_UNKNOWN"
    assert result.recommended_buyer_role == "UNKNOWN"


@pytest.mark.parametrize(
    ("score", "coverage", "expected"),
    [
        (65, 100, QualificationStatus.QUALIFIED),
        (45, 50, QualificationStatus.NURTURE),
    ],
)
def test_medium_fit_statuses(session, score, coverage, expected):
    svc, rel, _product, _icp, assessment = setup(
        session, score=score, coverage=coverage, confidence="MEDIUM"
    )
    assert svc.execute(rel.id, assessment.id).status == expected


def test_required_no_match_is_not_qualified(session):
    mismatch = [
        {
            "criterion_id": "industry",
            "type": "industry",
            "expected_value": "Software",
            "result": "NO_MATCH",
            "required": True,
            "fact_key": "industry",
            "actual_value": "safe normalized",
        }
    ]
    svc, rel, _product, _icp, assessment = setup(session, score=20, coverage=100, criteria=mismatch)
    assert svc.execute(rel.id, assessment.id).status == QualificationStatus.NOT_QUALIFIED


def test_intelligence_changed_and_icp_superseded_are_stale(session):
    svc, rel, product, icp, assessment = setup(session)
    assessment.evidence_snapshot[0]["selected_fact_id"] = "old"
    assert svc.execute(rel.id, assessment.id).status == QualificationStatus.STALE
    svc2, rel2, product2, icp2, assessment2 = setup(session)
    icp2.is_active = False
    replacement = ICP(
        organization_id=product2.organization_id,
        product=product2,
        name=icp2.name,
        version=2,
        logical_id=icp2.id,
        is_active=True,
    )
    session.add(replacement)
    session.flush()
    stale = svc2.execute(rel2.id, assessment2.id)
    assert stale.status == QualificationStatus.STALE
    assert stale.research_gaps[0]["code"] == "FIT_ASSESSMENT_STALE"


def test_reevaluation_is_new_row_and_old_is_immutable(session):
    svc, rel, _product, _icp, assessment = setup(session, roles=["CEO"])
    old = svc.execute(rel.id, assessment.id)
    snapshot = (old.id, old.qualification_score, old.recommended_buyer_role)
    new = svc.reevaluate(rel.id, old.id)
    assert new.id != old.id
    assert snapshot == (old.id, old.qualification_score, old.recommended_buyer_role)


def test_rbac_and_tenant_isolation(session):
    read, rel, _product, _icp, assessment = setup(session, role=MembershipRole.READ_ONLY)
    with pytest.raises(AuthorizationError):
        read.execute(rel.id, assessment.id)
    marketing, other_rel, _p, _i, other_assessment = setup(session)
    assert marketing.execute(other_rel.id, other_assessment.id)
    with pytest.raises(NotFoundError):
        marketing.get(rel.id, "missing")
    with pytest.raises(NotFoundError):
        marketing.execute(rel.id, other_assessment.id)
