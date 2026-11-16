"""Safe steps use Hunter company research when credits are allowed and public facts are missing."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import select

import marketingiq.domain.campaigns  # noqa: F401  (registers tables referenced by other models)
from marketingiq.application.orchestration import AutomationOrchestrationService
from marketingiq.application.research import ProviderRegistry
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.models import (
    ICP,
    Company,
    CompanyIdentifier,
    LeadQualification,
    MembershipRole,
    Organization,
    OrganizationCompany,
    Product,
    ProductStatus,
    ResearchMode,
    ResearchRun,
    User,
)
from marketingiq.domain.providers import ProviderCapability, ProviderFact, ProviderResult


class Provider:
    capabilities = frozenset({ProviderCapability.ENRICH_COMPANY})

    def __init__(self, key, facts, configured=True):
        self.key = key
        self.costs_credits = key != "PUBLIC_WEB"
        self.configured = configured
        self.facts = facts
        self.calls = 0

    def search_contacts(self, domain, max_results):
        self.contact_searches = getattr(self, "contact_searches", 0) + 1
        return SimpleNamespace(
            contacts=(), request_identifier=None, credits_used=0, credits_remaining=None
        )

    def enrich_company(self, domain):
        self.calls += 1
        now = datetime.now(UTC)
        return ProviderResult(
            provider_key=self.key,
            facts=tuple(ProviderFact(k, v, 80, f"https://{domain}/", now) for k, v in self.facts),
            credits_used=1 if self.costs_credits else None,
        )


def seed(session):
    suffix = uuid4().hex[:8]
    org = Organization(name="Auto", slug="auto-" + suffix)
    user = User(email=f"auto-{suffix}@example.test", password_hash="x")
    company = Company(canonical_name="Port Alpha")
    company.identifiers.append(
        CompanyIdentifier(
            kind="DOMAIN",
            value=f"portalpha-{suffix}.example",
            normalized_value=f"portalpha-{suffix}.example",
            is_primary=True,
        )
    )
    relationship = OrganizationCompany(
        organization=org, company=company, lifecycle_status="discovered"
    )
    product = Product(
        organization=org,
        name="CardIQ",
        slug="cardiq-" + suffix,
        value_proposition="Digital cards",
        status=ProductStatus.ACTIVE,
    )
    session.add_all([user, relationship, product])
    session.flush()
    icp = ICP(
        organization_id=org.id,
        product=product,
        name="Mid-size",
        version=1,
        is_active=True,
        employee_min=50,
    )
    session.add(icp)
    session.flush()
    icp.logical_id = icp.id
    session.flush()
    return org, user, relationship, product


def orchestration(session, org, user, providers, role=MembershipRole.ORGANIZATION_ADMIN):
    return AutomationOrchestrationService(
        session, TenantContext(org.id, user.id, role), ProviderRegistry(providers)
    )


def qualification_status(session, relationship):
    item = session.scalar(
        select(LeadQualification)
        .where(LeadQualification.organization_company_id == relationship.id)
        .order_by(LeadQualification.qualified_at.desc(), LeadQualification.id.desc())
    )
    return item.status.value if item else None


def test_without_credits_public_research_alone_leaves_size_unknown(session):
    org, user, relationship, product = seed(session)
    public = Provider("PUBLIC_WEB", [("company_description", "Container terminal operator")])
    hunter = Provider("HUNTER", [("employee_count", 400), ("industry", "Maritime")])
    svc = orchestration(session, org, user, [public, hunter])

    plan = svc.plan(relationship.id, product.id)
    assert plan["steps"][0]["requires_provider_credits"] is True
    assert plan["external_research"] == "AVAILABLE"

    result = svc.run_until_gate(relationship.id, product.id)

    assert hunter.calls == 0
    assert qualification_status(session, relationship) == "NEEDS_MORE_RESEARCH"
    assert "Allow provider credits" in result["plan"]["next_action_hint"]
    modes = set(session.scalars(select(ResearchRun.mode)))
    assert modes == {ResearchMode.PUBLIC_ONLY}


def test_with_credits_hunter_fills_size_and_company_qualifies(session):
    org, user, relationship, product = seed(session)
    public = Provider("PUBLIC_WEB", [("company_description", "Container terminal operator")])
    hunter = Provider("HUNTER", [("employee_count", 400), ("industry", "Maritime")])
    svc = orchestration(session, org, user, [public, hunter])

    result = svc.run_until_gate(relationship.id, product.id, allow_provider_credits=True)

    assert hunter.calls == 1
    assert hunter.contact_searches == 1  # the run continues to people search once qualified
    assert [s["step"] for s in result["executed_steps"]][:3] == [
        "RESEARCH_PUBLIC",
        "ASSESS_FIT",
        "QUALIFY",
    ]
    assert qualification_status(session, relationship) in {"QUALIFIED", "HIGH_PRIORITY"}
    assert set(session.scalars(select(ResearchRun.mode))) == {ResearchMode.PUBLIC_THEN_EXTERNAL}
    assert svc.plan(relationship.id, product.id)["external_research"] == "RECENT"

    # A second run within the freshness window does not call Hunter again.
    svc.run_until_gate(relationship.id, product.id, allow_provider_credits=True)
    assert hunter.calls == 1


def test_recent_hunter_research_that_still_misses_facts_gives_a_manual_hint(session):
    org, user, relationship, product = seed(session)
    public = Provider("PUBLIC_WEB", [("company_description", "Operator")])
    hunter = Provider("HUNTER", [("industry", "Maritime")])  # no headcount
    svc = orchestration(session, org, user, [public, hunter])

    result = svc.run_until_gate(relationship.id, product.id, allow_provider_credits=True)

    assert hunter.calls == 1
    assert qualification_status(session, relationship) == "NEEDS_MORE_RESEARCH"
    assert result["plan"]["external_research"] == "RECENT"
    assert "Research tab" in result["plan"]["next_action_hint"]


def test_marketing_users_and_unconfigured_hunter_never_trigger_external_research(session):
    org, user, relationship, product = seed(session)
    public = Provider("PUBLIC_WEB", [("company_description", "Operator")])
    hunter = Provider("HUNTER", [("employee_count", 400)])
    marketer = orchestration(
        session, org, user, [public, hunter], role=MembershipRole.MARKETING_USER
    )
    marketer.run_until_gate(relationship.id, product.id, allow_provider_credits=True)
    assert hunter.calls == 0
    assert marketer.plan(relationship.id, product.id)["external_research"] == "UNAVAILABLE"

    org2, user2, relationship2, product2 = seed(session)
    off = Provider("HUNTER", [("employee_count", 400)], configured=False)
    admin = orchestration(session, org2, user2, [public, off])
    admin.run_until_gate(relationship2.id, product2.id, allow_provider_credits=True)
    assert off.calls == 0
    assert "HUNTER_API_KEY" in admin.plan(relationship2.id, product2.id)["next_action_hint"]
