from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from marketingiq.application.errors import AuthorizationError
from marketingiq.application.intelligence import IntelligenceService, ReviewRequest
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.models import (
    AuditLog,
    Company,
    CompanyFact,
    DataClassification,
    DataSource,
    Evidence,
    HumanOverride,
    MembershipRole,
    Organization,
    OrganizationCompany,
    RedistributionStatus,
    ReviewAction,
    User,
)

NOW = datetime(2026, 9, 15, tzinfo=UTC)


def setup_tenant(session, role=MembershipRole.ORGANIZATION_ADMIN):
    org = Organization(name="Acme tenant", slug=f"acme-{role.value.lower()}")
    user = User(email=f"{role.value.lower()}@example.com", password_hash="x")
    company = Company(canonical_name="Target")
    session.add_all([org, user, company])
    session.flush()
    relationship = OrganizationCompany(organization_id=org.id, company_id=company.id)
    session.add(relationship)
    session.flush()
    context = TenantContext(org.id, user.id, role)
    return IntelligenceService(session, context, now=NOW), relationship, company, org, user


def fact(session, company, key, value, classification, confidence, age=1, org_id=None, status=None):
    item = CompanyFact(
        company_id=company.id,
        organization_id=org_id,
        fact_key=key,
        value=value,
        classification=classification,
        redistribution_status=status or RedistributionStatus.ALLOWED,
        confidence=confidence,
        observed_at=NOW - timedelta(days=age),
    )
    session.add(item)
    session.flush()
    return item


def test_policy_is_repeatable_and_new_low_confidence_does_not_win(session):
    service, relationship, company, *_ = setup_tenant(session)
    older = fact(
        session, company, "industry", "Logistics", DataClassification.PUBLIC_EVIDENCE, 95, 100
    )
    fact(session, company, "industry", "Software", DataClassification.PUBLIC_EVIDENCE, 40, 1)

    first = service.get(relationship.id, "industry")
    second = service.get(relationship.id, "industry")
    assert first["selected_fact_id"] == older.id == second["selected_fact_id"]
    assert first["conflict_status"] == "NONE"


def test_customer_precedes_public_and_licensed_while_rights_are_preserved(session):
    service, relationship, company, org, _ = setup_tenant(session)
    source = DataSource(provider_key="VENDOR", display_name="Licensed vendor")
    session.add(source)
    licensed = fact(
        session,
        company,
        "country",
        "AE",
        DataClassification.THIRD_PARTY_LICENSED,
        100,
        status=RedistributionStatus.RESTRICTED,
    )
    licensed.evidences.append(
        Evidence(data_source_id=source.id, reference_url="https://vendor.test")
    )
    fact(session, company, "country", "SA", DataClassification.PUBLIC_EVIDENCE, 90)
    customer = fact(
        session, company, "country", "QA", DataClassification.CUSTOMER_PROVIDED, 70, org_id=org.id
    )
    assert service.get(relationship.id, "country")["selected_fact_id"] == customer.id

    service.review(
        relationship.id,
        "country",
        ReviewRequest(ReviewAction.SELECT, licensed.id, note="contract permits internal use"),
    )
    projected = service.get(relationship.id, "country")
    assert projected["redistribution_status"] == RedistributionStatus.RESTRICTED
    assert projected["evidence_summary"][0]["source_url"] is None


def test_conflict_staleness_and_unknown_boolean(session):
    service, relationship, company, *_ = setup_tenant(session)
    fact(session, company, "employee_range", "100-250", DataClassification.PUBLIC_EVIDENCE, 90)
    fact(session, company, "employee_range", "1000-5000", DataClassification.PUBLIC_EVIDENCE, 85)
    result = service.get(relationship.id, "employee_range")
    assert result["conflict_status"] == "NEEDS_REVIEW"
    assert result["review_status"] == "CONFLICT"

    fact(session, company, "active_hiring", None, DataClassification.PUBLIC_EVIDENCE, 90, 31)
    hiring = service.get(relationship.id, "active_hiring")
    assert hiring["value"] is None
    assert hiring["staleness_status"] == "STALE"
    assert hiring["review_status"] == "STALE_REVIEW_REQUIRED"


def test_manual_correction_is_append_only_audited_and_revocable(session):
    service, relationship, company, *_ = setup_tenant(session)
    original = fact(
        session, company, "industry", "Logistics", DataClassification.PUBLIC_EVIDENCE, 90
    )
    reviewed = service.review(
        relationship.id,
        "industry",
        ReviewRequest(
            ReviewAction.MANUAL_CORRECTION,
            value="Supply chain",
            note="Confirmed by customer",
            evidence_url="https://target.test/about",
        ),
    )
    assert reviewed["selected_fact_id"] != original.id
    assert len(service.history(relationship.id, "industry")) == 2
    assert reviewed["review_status"] == "OVERRIDDEN"
    revoked = service.revoke(relationship.id, "industry")
    # Revocation clears the explicit override, but cannot delete the appended correction.
    assert revoked["selection_reason"].startswith("POLICY_CUSTOMER_PROVIDED")
    assert revoked["review_status"] == "CONFLICT"
    assert session.scalars(select(HumanOverride)).one().revoked_at is not None
    assert len(session.scalars(select(AuditLog)).all()) == 2


@pytest.mark.parametrize("role", [MembershipRole.READ_ONLY, MembershipRole.MARKETING_USER])
def test_non_admin_can_read_but_cannot_review(session, role):
    service, relationship, company, *_ = setup_tenant(session, role)
    item = fact(session, company, "country", "SA", DataClassification.PUBLIC_EVIDENCE, 90)
    assert service.get(relationship.id, "country")["value"] == "SA"
    with pytest.raises(AuthorizationError):
        service.review(relationship.id, "country", ReviewRequest(ReviewAction.APPROVE, item.id))


def test_tenant_isolation_applies_to_history_and_reviews(session):
    service, relationship, company, *_ = setup_tenant(session)
    other = Organization(name="Other", slug="other")
    session.add(other)
    session.flush()
    fact(session, company, "country", "SA", DataClassification.PUBLIC_EVIDENCE, 80)
    fact(
        session,
        company,
        "country",
        "SECRET",
        DataClassification.CUSTOMER_PROVIDED,
        100,
        org_id=other.id,
    )
    assert [item.value for item in service.history(relationship.id, "country")] == ["SA"]
    with pytest.raises(LookupError):
        service.get("not-this-tenants-relationship", "country")


def test_approve_and_resolve_conflict(session):
    service, relationship, company, *_ = setup_tenant(session)
    selected = fact(session, company, "country", "SA", DataClassification.PUBLIC_EVIDENCE, 95)
    fact(session, company, "country", "AE", DataClassification.PUBLIC_EVIDENCE, 90)
    approved = service.review(
        relationship.id, "country", ReviewRequest(ReviewAction.APPROVE, selected.id)
    )
    assert approved["review_status"] == "APPROVED"
    resolved = service.review(
        relationship.id,
        "country",
        ReviewRequest(ReviewAction.RESOLVE_CONFLICT, selected.id, note="Verified registration"),
    )
    assert resolved["conflict_status"] == "NONE"
    assert resolved["review_status"] == "OVERRIDDEN"
