from datetime import UTC, datetime

import pytest

from marketingiq.application.errors import AuthorizationError
from marketingiq.application.research import CompanyResearchService, ProviderRegistry
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.models import (
    Company,
    CompanyFact,
    CompanyIdentifier,
    MembershipRole,
    Organization,
    OrganizationCompany,
    ProviderUsage,
    ResearchMode,
    ResearchStatus,
    User,
)
from marketingiq.domain.providers import (
    ProviderCapability,
    ProviderError,
    ProviderFact,
    ProviderResult,
)


class FakeProvider:
    capabilities = frozenset({ProviderCapability.ENRICH_COMPANY})

    def __init__(self, key="PUBLIC_WEB", fail=False, configured=True):
        self.key = key
        self.costs_credits = key != "PUBLIC_WEB"
        self._configured = configured
        self.fail = fail
        self.calls = 0

    @property
    def configured(self):
        return self._configured

    def enrich_company(self, domain):
        self.calls += 1
        if self.fail:
            raise ProviderError("safe failure")
        return ProviderResult(
            provider_key=self.key,
            facts=(
                ProviderFact("industry", "Software", 80, f"https://{domain}/", datetime.now(UTC)),
            ),
            credits_used=1 if self.costs_credits else None,
        )


def setup_tenant(session, role=MembershipRole.ORGANIZATION_ADMIN, suffix=""):
    org = Organization(name=f"Org{suffix}", slug=f"org{suffix}")
    user = User(email=f"user{suffix}@test.com", password_hash="unused")
    company = Company(canonical_name="Acme")
    company.identifiers.append(
        CompanyIdentifier(
            kind="DOMAIN", value="acme.test", normalized_value=f"acme{suffix}.test", is_primary=True
        )
    )
    relationship = OrganizationCompany(organization=org, company=company)
    session.add_all([org, user, company, relationship])
    session.flush()
    return TenantContext(org.id, user.id, role), relationship


def test_registry_reports_capabilities_without_credentials():
    hunter = FakeProvider("HUNTER", configured=False)
    status = ProviderRegistry([hunter]).status()[0]
    assert status["status"] == "not_configured"
    assert status["costs_credits"] is True
    assert "api_key" not in status


def test_public_only_creates_append_only_fact_evidence_and_usage(session):
    tenant, relationship = setup_tenant(session)
    provider = FakeProvider()
    service = CompanyResearchService(session, tenant, ProviderRegistry([provider]))
    first = service.run(relationship.id, force_refresh=True)
    second = service.run(relationship.id, force_refresh=True)
    session.flush()
    assert first.status == ResearchStatus.COMPLETED
    assert second.status == ResearchStatus.COMPLETED
    assert len(session.query(CompanyFact).all()) == 2
    assert all(fact.evidences for fact in session.query(CompanyFact).all())
    assert len(session.query(ProviderUsage).all()) == 2


def test_successful_provider_call_is_cached_and_spends_no_credit(session):
    tenant, relationship = setup_tenant(session)
    provider = FakeProvider("HUNTER")
    service = CompanyResearchService(session, tenant, ProviderRegistry([provider]))
    service.run(relationship.id, ResearchMode.EXTERNAL_ONLY)
    service.run(relationship.id, ResearchMode.EXTERNAL_ONLY)
    usages = session.query(ProviderUsage).order_by(ProviderUsage.requested_at).all()
    assert provider.calls == 1
    assert usages[-1].cache_hit is True
    assert usages[-1].credits_used == 0


def test_free_first_default_never_calls_external_provider(session):
    tenant, relationship = setup_tenant(session)
    public, hunter = FakeProvider(), FakeProvider("HUNTER")
    service = CompanyResearchService(session, tenant, ProviderRegistry([public, hunter]))
    run = service.run(relationship.id)
    assert run.providers_attempted == ["PUBLIC_WEB"]
    assert hunter.calls == 0


def test_provider_failure_yields_partial_and_preserves_public_fact(session):
    tenant, relationship = setup_tenant(session)
    public, hunter = FakeProvider(), FakeProvider("HUNTER", fail=True)
    service = CompanyResearchService(session, tenant, ProviderRegistry([public, hunter]))
    run = service.run(relationship.id, ResearchMode.PUBLIC_THEN_EXTERNAL, force_refresh=True)
    assert run.status == ResearchStatus.PARTIAL
    assert run.providers_succeeded == ["PUBLIC_WEB"]
    assert session.query(CompanyFact).count() == 1
    assert "safe failure" not in (run.error_summary or "")


@pytest.mark.parametrize("mode", [ResearchMode.PUBLIC_THEN_EXTERNAL, ResearchMode.EXTERNAL_ONLY])
def test_marketing_user_cannot_spend_external_credits(session, mode):
    tenant, relationship = setup_tenant(session, MembershipRole.MARKETING_USER)
    service = CompanyResearchService(
        session, tenant, ProviderRegistry([FakeProvider(), FakeProvider("HUNTER")])
    )
    with pytest.raises(AuthorizationError):
        service.run(relationship.id, mode)


def test_read_only_cannot_run_research(session):
    tenant, relationship = setup_tenant(session, MembershipRole.READ_ONLY)
    with pytest.raises(AuthorizationError):
        CompanyResearchService(session, tenant, ProviderRegistry([FakeProvider()])).run(
            relationship.id
        )


def test_research_runs_are_tenant_isolated(session):
    tenant_a, relationship = setup_tenant(session, suffix="a")
    tenant_b, _ = setup_tenant(session, suffix="b")
    service_a = CompanyResearchService(session, tenant_a, ProviderRegistry([FakeProvider()]))
    service_a.run(relationship.id)
    service_b = CompanyResearchService(session, tenant_b, ProviderRegistry([FakeProvider()]))
    with pytest.raises(LookupError):
        service_b.list_runs(relationship.id)
