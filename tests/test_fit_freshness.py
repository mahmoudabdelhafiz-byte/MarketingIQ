from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from marketingiq.application.errors import AuthorizationError, NotFoundError
from marketingiq.application.fit import FitAssessmentService
from marketingiq.application.fit_freshness import (
    FitAssessmentFreshnessService,
    FreshnessStatus,
)
from marketingiq.application.intelligence import IntelligenceService, ReviewRequest
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
    ReviewAction,
    User,
)

NOW = datetime(2026, 9, 15, tzinfo=UTC)


def scenario(session, role=MembershipRole.ORGANIZATION_ADMIN):
    suffix = uuid4().hex
    org = Organization(name="Fresh Tenant", slug=f"fresh-{suffix}")
    user = User(email=f"fresh-{suffix}@test", password_hash="x")
    company = Company(canonical_name="Target")
    relationship = OrganizationCompany(organization=org, company=company)
    product = Product(organization=org, name="Product", slug=f"product-{suffix}")
    icp = ICP(organization_id=org.id, product=product, name="Mid-market", version=1)
    icp.criteria = [ICPCriterion(kind="industry", value="Software", weight=3, required=False)]
    session.add_all([user, relationship, product])
    session.flush()
    context = TenantContext(org.id, user.id, role)
    return context, relationship, product, icp


def fact(session, relationship, value, *, age=1, confidence=90):
    item = CompanyFact(
        company_id=relationship.company_id,
        fact_key="industry",
        value=value,
        classification=DataClassification.PUBLIC_EVIDENCE,
        redistribution_status=RedistributionStatus.ALLOWED,
        confidence=confidence,
        observed_at=NOW - timedelta(days=age),
    )
    session.add(item)
    session.flush()
    return item


def assess(session, context, relationship, product, icp):
    return FitAssessmentService(session, context, NOW).evaluate(relationship.id, product.id, icp.id)


def freshness(session, context, assessment, *, now=NOW):
    return FitAssessmentFreshnessService(session, context, now).get(
        assessment.organization_company_id, assessment.id
    )


def test_immediate_assessment_is_current_and_repeatable(session):
    context, relationship, product, icp = scenario(session)
    fact(session, relationship, "Software")
    assessment = assess(session, context, relationship, product, icp)

    first = freshness(session, context, assessment)
    second = freshness(session, context, assessment)

    assert first == second
    assert first["freshness_status"] == FreshnessStatus.CURRENT
    assert first["is_current"] is True
    assert first["reasons"] == []


def test_new_selected_fact_and_unknown_to_known_are_intelligence_changes(session):
    context, relationship, product, icp = scenario(session)
    unknown = assess(session, context, relationship, product, icp)
    new_fact = fact(session, relationship, "Software", confidence=95)
    known = freshness(session, context, unknown)
    assert known["freshness_status"] == FreshnessStatus.INTELLIGENCE_CHANGED
    assert known["reasons"][0]["code"] == "UNKNOWN_BECAME_KNOWN"
    assert known["reasons"][0]["current_fact_id"] == new_fact.id

    current = assess(session, context, relationship, product, icp)
    newer = fact(session, relationship, "Banking", confidence=99)
    changed = freshness(session, context, current)
    assert changed["intelligence_changed"] is True
    assert changed["reasons"][0]["code"] == "SELECTED_FACT_CHANGED"
    assert changed["reasons"][0]["current_fact_id"] == newer.id


def test_human_override_and_revocation_change_projection(session):
    context, relationship, product, icp = scenario(session)
    automatic = fact(session, relationship, "Software", confidence=95)
    alternative = fact(session, relationship, "Logistics", confidence=80)
    assessment = assess(session, context, relationship, product, icp)
    intelligence = IntelligenceService(session, context, now=NOW)
    intelligence.review(
        relationship.id,
        "industry",
        ReviewRequest(ReviewAction.RESOLVE_CONFLICT, selected_fact_id=alternative.id),
    )
    overridden = freshness(session, context, assessment)
    assert overridden["intelligence_changed"] is True
    assert overridden["reasons"][0]["current_fact_id"] == alternative.id

    overridden_assessment = assess(session, context, relationship, product, icp)
    intelligence.revoke(relationship.id, "industry")
    revoked = freshness(session, context, overridden_assessment)
    assert revoked["intelligence_changed"] is True
    assert revoked["reasons"][0]["current_fact_id"] == automatic.id


def test_same_fact_staleness_and_new_conflict_are_quality_changes(session):
    context, relationship, product, icp = scenario(session)
    selected = fact(session, relationship, "Software", age=729, confidence=95)
    assessment = assess(session, context, relationship, product, icp)
    stale = freshness(session, context, assessment, now=NOW + timedelta(days=2))
    assert stale["freshness_status"] == FreshnessStatus.QUALITY_CHANGED
    assert stale["reasons"][0]["code"] == "QUALITY_CURRENT_TO_STALE"

    selected.observed_at = NOW - timedelta(days=1)
    clean = assess(session, context, relationship, product, icp)
    fact(session, relationship, "Banking", confidence=90)
    conflict = freshness(session, context, clean)
    assert conflict["quality_changed"] is True
    assert conflict["reasons"][0]["current_fact_id"] == selected.id
    assert conflict["reasons"][0]["current_quality"] == "CONFLICTED"


def test_same_fact_review_and_confidence_changes_are_quality_changes(session):
    context, relationship, product, icp = scenario(session)
    selected = fact(session, relationship, "Software", confidence=90)
    assessment = assess(session, context, relationship, product, icp)

    intelligence = IntelligenceService(session, context, now=NOW)
    intelligence.review(
        relationship.id,
        "industry",
        ReviewRequest(ReviewAction.APPROVE, selected_fact_id=selected.id),
    )
    reviewed = freshness(session, context, assessment)
    assert reviewed["freshness_status"] == FreshnessStatus.QUALITY_CHANGED
    assert reviewed["reasons"][0]["code"] == "HUMAN_OVERRIDE_CHANGED"

    reviewed_assessment = assess(session, context, relationship, product, icp)
    selected.confidence = 75
    confidence = freshness(session, context, reviewed_assessment)
    assert confidence["freshness_status"] == FreshnessStatus.QUALITY_CHANGED
    assert confidence["reasons"][0]["code"] == "EVIDENCE_CONFIDENCE_CHANGED"


def test_icp_lineage_supersedes_but_unrelated_icp_does_not(session):
    context, relationship, product, icp = scenario(session)
    fact(session, relationship, "Software")
    icp.logical_id = icp.id
    assessment = assess(session, context, relationship, product, icp)
    unrelated = ICP(
        organization_id=context.organization_id,
        product_id=product.id,
        name="Enterprise",
        version=9,
        is_active=True,
    )
    session.add(unrelated)
    session.flush()
    assert freshness(session, context, assessment)["icp_changed"] is False

    icp.is_active = False
    revision = ICP(
        organization_id=context.organization_id,
        product_id=product.id,
        name="Renamed segment",
        version=2,
        logical_id=icp.id,
        is_active=True,
    )
    session.add(revision)
    session.flush()
    result = freshness(session, context, assessment)
    assert result["freshness_status"] == FreshnessStatus.ICP_CHANGED
    assert result["current_icp_id"] == revision.id
    assert result["current_icp_version"] == 2


def test_both_changes_reassessment_is_new_and_old_is_immutable(session):
    context, relationship, product, icp = scenario(session)
    fact(session, relationship, "Software", confidence=90)
    icp.logical_id = icp.id
    old = assess(session, context, relationship, product, icp)
    old_state = deepcopy((old.evidence_snapshot, old.explanation, old.score))
    fact(session, relationship, "Banking", confidence=99)
    icp.is_active = False
    revision = ICP(
        organization_id=context.organization_id,
        product_id=product.id,
        name=icp.name,
        version=2,
        logical_id=icp.id,
        is_active=True,
    )
    revision.criteria = [ICPCriterion(kind="industry", value="Banking", weight=3, required=False)]
    session.add(revision)
    session.flush()
    state = freshness(session, context, old)
    assert state["freshness_status"] == FreshnessStatus.INTELLIGENCE_AND_ICP_CHANGED

    marketing = TenantContext(
        context.organization_id, context.actor_user_id, MembershipRole.MARKETING_USER
    )
    new = FitAssessmentService(session, marketing, NOW).evaluate(
        relationship.id, product.id, state["current_icp_id"]
    )
    assert new.id != old.id
    assert (old.evidence_snapshot, old.explanation, old.score) == old_state


def test_read_only_can_inspect_but_not_reassess_and_tenant_isolation(session):
    admin, relationship, product, icp = scenario(session)
    assessment = assess(session, admin, relationship, product, icp)
    read = TenantContext(admin.organization_id, admin.actor_user_id, MembershipRole.READ_ONLY)
    assert freshness(session, read, assessment)["is_current"] is True
    with pytest.raises(AuthorizationError):
        FitAssessmentService(session, read, NOW).evaluate(relationship.id, product.id, icp.id)

    other, other_relationship, _, _ = scenario(session)
    with pytest.raises(NotFoundError):
        FitAssessmentFreshnessService(session, other, NOW).get(other_relationship.id, assessment.id)


def test_freshness_reasons_do_not_leak_restricted_evidence(session):
    context, relationship, product, icp = scenario(session)
    restricted = fact(session, relationship, "Software")
    restricted.classification = DataClassification.THIRD_PARTY_LICENSED
    restricted.redistribution_status = RedistributionStatus.RESTRICTED
    assessment = assess(session, context, relationship, product, icp)
    result = freshness(session, context, assessment)
    serialized = str(result).casefold()
    assert "payload" not in serialized
    assert "evidence" not in serialized
    assert "url" not in serialized
