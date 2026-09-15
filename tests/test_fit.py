from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from marketingiq.application.errors import AuthorizationError, NotFoundError
from marketingiq.application.fit import FitAssessmentService
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.models import (
    ICP,
    AuditLog,
    Company,
    CompanyFact,
    DataClassification,
    FitGrade,
    FitStatus,
    ICPCriterion,
    MembershipRole,
    Organization,
    OrganizationCompany,
    Product,
    RedistributionStatus,
    User,
)

NOW = datetime(2026, 9, 15, tzinfo=UTC)


def setup(session, role=MembershipRole.MARKETING_USER, criteria=()):
    org = Organization(name="Tenant", slug=f"tenant-{role.value.lower()}-{len(session.new)}")
    user = User(email=f"{role.value.lower()}-{len(session.new)}@test", password_hash="x")
    company = Company(canonical_name="Target")
    product = Product(organization=org, name="Product", slug="product")
    icp = ICP(organization_id=org.id, product=product, name="ICP", version=1)
    icp.criteria = [ICPCriterion(kind=k, value=v, weight=w, required=r) for k, v, w, r in criteria]
    relationship = OrganizationCompany(organization=org, company=company)
    session.add_all([user, product, relationship])
    session.flush()
    return (
        FitAssessmentService(session, TenantContext(org.id, user.id, role), NOW),
        relationship,
        product,
        icp,
    )


def add_fact(session, relationship, key, value, *, age=1, confidence=90):
    item = CompanyFact(
        company_id=relationship.company_id,
        fact_key=key,
        value=value,
        classification=DataClassification.PUBLIC_EVIDENCE,
        redistribution_status=RedistributionStatus.ALLOWED,
        confidence=confidence,
        observed_at=NOW - timedelta(days=age),
    )
    session.add(item)
    session.flush()
    return item


def test_perfect_match_is_repeatable_historical_and_derived(session):
    svc, rel, product, icp = setup(
        session, criteria=[("industry", "Software", 5, True), ("country", "US", 2, False)]
    )
    add_fact(session, rel, "industry", "Software")
    add_fact(session, rel, "country", "US")
    first = svc.evaluate(rel.id, product.id, icp.id)
    second = svc.evaluate(rel.id, product.id, icp.id)
    assert first.id != second.id
    assert (first.score, first.evidence_coverage, first.grade, first.status) == (
        100,
        100,
        FitGrade.A,
        FitStatus.COMPLETE,
    )
    assert first.explanation == second.explanation
    assert first.classification == DataClassification.MARKETINGIQ_DERIVED
    assert len(svc.list(rel.id)) == 2
    audit = session.scalars(
        select(AuditLog).where(AuditLog.action == "fit_assessment.executed")
    ).first()
    assert set(audit.metadata_json) == {
        "company_id",
        "product_id",
        "icp_id",
        "assessment_id",
        "score",
        "status",
    }


def test_unknown_is_not_failure_and_exposes_low_coverage(session):
    svc, rel, product, icp = setup(
        session, criteria=[("industry", "Software", 1, False), ("country", "US", 3, False)]
    )
    add_fact(session, rel, "industry", "Software")
    result = svc.evaluate(rel.id, product.id, icp.id)
    assert result.score == 100
    assert result.evidence_coverage == 25
    assert result.confidence == "LOW"
    assert result.status == FitStatus.PARTIAL
    assert result.explanation["unknown_weight"] == 3


def test_required_mismatch_caps_and_required_unknown_requests_research(session):
    svc, rel, product, icp = setup(
        session, criteria=[("industry", "Software", 5, True), ("country", "US", 1, False)]
    )
    add_fact(session, rel, "industry", "Banking")
    add_fact(session, rel, "country", "US")
    mismatch = svc.evaluate(rel.id, product.id, icp.id)
    assert mismatch.score == 17 and mismatch.grade == FitGrade.D
    svc2, rel2, product2, icp2 = setup(session, criteria=[("industry", "Software", 5, True)])
    unknown = svc2.evaluate(rel2.id, product2.id, icp2.id)
    assert unknown.grade == FitGrade.UNKNOWN
    assert unknown.status == FitStatus.NEEDS_MORE_RESEARCH


def test_stale_and_conflicted_current_best_quality(session):
    svc, rel, product, icp = setup(session, criteria=[("active_hiring", "true", 1, False)])
    add_fact(session, rel, "active_hiring", True, age=31)
    assert svc.evaluate(rel.id, product.id, icp.id).status == FitStatus.STALE
    svc2, rel2, product2, icp2 = setup(session, criteria=[("industry", "Software", 1, False)])
    add_fact(session, rel2, "industry", "Software", confidence=95)
    add_fact(session, rel2, "industry", "Banking", confidence=90)
    assert svc2.evaluate(rel2.id, product2.id, icp2.id).status == FitStatus.CONFLICTED


def test_rbac_tenant_isolation_and_version_capture(session):
    read, rel, product, icp = setup(
        session, MembershipRole.READ_ONLY, [("country", "US", 1, False)]
    )
    with pytest.raises(AuthorizationError):
        read.evaluate(rel.id, product.id, icp.id)
    admin, other_rel, other_product, other_icp = setup(
        session, MembershipRole.ORGANIZATION_ADMIN, [("country", "US", 1, False)]
    )
    other_icp.version = 7
    assessment = admin.evaluate(other_rel.id, other_product.id, other_icp.id)
    assert assessment.icp_version == 7
    with pytest.raises(NotFoundError):
        admin.get(other_rel.id, "foreign-assessment")
    with pytest.raises(NotFoundError):
        admin.get(rel.id, assessment.id)


def test_restricted_payload_is_not_snapshotted(session):
    svc, rel, product, icp = setup(session, criteria=[("business_service", "Payments", 1, False)])
    fact = add_fact(session, rel, "business_services", ["Payments"])
    fact.classification = DataClassification.THIRD_PARTY_LICENSED
    fact.redistribution_status = RedistributionStatus.RESTRICTED
    result = svc.evaluate(rel.id, product.id, icp.id)
    serialized = str(result.evidence_snapshot) + str(result.explanation)
    assert "provider" not in serialized.casefold()
    assert "payload" not in serialized.casefold()
