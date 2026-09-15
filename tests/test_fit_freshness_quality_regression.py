from datetime import UTC, datetime, timedelta
from uuid import uuid4

from marketingiq.application.fit import FitAssessmentService
from marketingiq.application.fit_freshness import (
    FitAssessmentFreshnessService,
    FreshnessStatus,
)
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.models import (
    ICP,
    Company,
    CompanyFact,
    DataClassification,
    ICPCriterion,
    MembershipRole,
    Organization,
    OrganizationCompany,
    Product,
    RedistributionStatus,
    User,
)

NOW = datetime(2026, 9, 15, tzinfo=UTC)


def test_same_selected_fact_confidence_change_is_quality_change(session):
    suffix = uuid4().hex
    org = Organization(name="Confidence Tenant", slug=f"confidence-{suffix}")
    user = User(email=f"confidence-{suffix}@test", password_hash="x")
    company = Company(canonical_name="Target")
    relationship = OrganizationCompany(organization=org, company=company)
    product = Product(organization=org, name="Product", slug=f"product-{suffix}")
    icp = ICP(organization_id=org.id, product=product, name="Mid-market", version=1)
    icp.criteria = [ICPCriterion(kind="industry", value="Software", weight=3, required=False)]
    session.add_all([user, relationship, product])
    session.flush()

    context = TenantContext(org.id, user.id, MembershipRole.ORGANIZATION_ADMIN)
    selected = CompanyFact(
        company_id=relationship.company_id,
        fact_key="industry",
        value="Software",
        classification=DataClassification.PUBLIC_EVIDENCE,
        redistribution_status=RedistributionStatus.ALLOWED,
        confidence=95,
        observed_at=NOW - timedelta(days=1),
    )
    session.add(selected)
    session.flush()

    assessment = FitAssessmentService(session, context, NOW).evaluate(
        relationship.id, product.id, icp.id
    )
    selected_id = assessment.evidence_snapshot[0]["selected_fact_id"]

    selected.confidence = 90
    session.flush()

    result = FitAssessmentFreshnessService(session, context, NOW).get(
        relationship.id, assessment.id
    )

    assert result["freshness_status"] == FreshnessStatus.QUALITY_CHANGED
    assert result["intelligence_changed"] is False
    assert result["quality_changed"] is True
    assert result["reasons"][0]["code"] == "PROJECTION_QUALITY_CHANGED"
    assert result["reasons"][0]["previous_fact_id"] == selected_id
    assert result["reasons"][0]["current_fact_id"] == selected_id
