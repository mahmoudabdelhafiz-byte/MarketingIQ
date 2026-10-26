from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from marketingiq.api.app import create_app
from marketingiq.application.auth import hash_password
from marketingiq.application.discovery import (
    CompanyDiscoveryExecutor,
    CompanyDiscoveryService,
    headcount_buckets,
    normalize_criteria,
)
from marketingiq.application.errors import AuthorizationError
from marketingiq.application.research import ProviderRegistry
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.discovery import (
    CompanyDiscoveryRun,
    CompanyDiscoveryRunStatus,
    DiscoveredCompany,
    DiscoveryCriteria,
    DiscoveryResult,
)
from marketingiq.domain.models import (
    ICP,
    Base,
    Company,
    CompanyIdentifier,
    ICPCriterion,
    MembershipRole,
    Organization,
    OrganizationCompany,
    OrganizationMembership,
    Product,
    User,
)
from marketingiq.infrastructure.discovery import (
    DiscoveryAccessDenied,
    DiscoveryInvalidFilter,
    DiscoveryPaginationUnavailable,
    HunterDiscoveryProvider,
    discover_request_body,
)

NOW = datetime(2026, 9, 23, 6, 0, tzinfo=UTC)
SECRET = "a-secure-test-secret-that-is-long-enough"
PASSWORD = "correct horse battery"


class FakeDiscovery:
    key = "HUNTER"

    def __init__(self, companies, *, total=None, errors=None, configured=True):
        self.companies = [DiscoveredCompany(d, n) for d, n in companies]
        self.total = total
        self.errors = list(errors or [])
        self.calls: list[tuple[DiscoveryCriteria, int]] = []
        self._configured = configured

    @property
    def configured(self):
        return self._configured

    def discover(self, criteria, offset=0):
        self.calls.append((criteria, offset))
        if self.errors:
            raise self.errors.pop(0)
        page = self.companies[offset : offset + 100]
        return DiscoveryResult("HUNTER", tuple(page), self.total)


def seed(session: Session, role=MembershipRole.ORGANIZATION_ADMIN):
    org = Organization(name="Barmageyat", slug="barmageyat")
    user = User(email="owner@example.test", password_hash=hash_password(PASSWORD))
    session.add_all([org, user])
    session.flush()
    session.add(OrganizationMembership(organization_id=org.id, user_id=user.id, role=role))
    product = Product(
        organization_id=org.id,
        name="CardIQ",
        slug="cardiq",
        value_proposition="Digital identity",
        primary_buyer_roles=["HR Director"],
    )
    session.add(product)
    session.flush()
    icp = ICP(
        organization_id=org.id,
        product_id=product.id,
        name="GCC logistics",
        employee_min=200,
        employee_max=5000,
        is_active=True,
    )
    icp.criteria = [
        ICPCriterion(kind="country", value="sa", weight=3),
        ICPCriterion(kind="country", value="AE", weight=3),
        ICPCriterion(kind="industry", value="Logistics", weight=5),
        ICPCriterion(kind="location", value="Jeddah", weight=1),
    ]
    session.add(icp)
    session.commit()
    return {"org": org, "user": user, "product": product, "icp": icp}


def service(session, seeded, provider, role=MembershipRole.ORGANIZATION_ADMIN):
    return CompanyDiscoveryService(
        session,
        TenantContext(seeded["org"].id, seeded["user"].id, role),
        provider,
        ProviderRegistry([]),
        now=NOW,
    )


def attach_existing(session, seeded, domain, name="Existing Co"):
    company = Company(canonical_name=name)
    company.identifiers.append(
        CompanyIdentifier(kind="DOMAIN", value=domain, normalized_value=domain, is_primary=True)
    )
    session.add(company)
    session.flush()
    session.add(OrganizationCompany(organization_id=seeded["org"].id, company=company))
    session.commit()


def test_headcount_buckets_and_criteria_validation():
    assert headcount_buckets(200, 5000) == ("51-200", "201-500", "501-1000", "1001-5000")
    assert headcount_buckets(None, None) == ()
    assert headcount_buckets(20000, None) == ("10001+",)
    criteria = normalize_criteria(countries="sa, ae", keywords=["Logistics", "Logistics"])
    assert criteria.countries == ("SA", "AE")
    assert criteria.keywords == ("Logistics",)
    with pytest.raises(ValueError, match="ISO"):
        normalize_criteria(countries=["Saudi"])
    with pytest.raises(ValueError, match="ISO"):
        normalize_criteria(countries=["S1"])
    with pytest.raises(ValueError, match="headcounts"):
        normalize_criteria(headcounts=["lots"])
    with pytest.raises(ValueError, match="at least one"):
        normalize_criteria()


def test_request_body_matches_hunter_discover_format():
    body = discover_request_body(
        DiscoveryCriteria(
            countries=("SA",),
            industries=("Logistics",),
            keywords=("ports",),
            headcounts=("201-500", "bogus"),
        )
    )
    assert body == {
        "headquarters_location": {"include": [{"country": "SA"}]},
        "industry": {"include": ["Logistics"]},
        "headcount": ["201-500"],
        "keywords": {"include": ["ports"], "match": "any"},
    }
    assert discover_request_body(DiscoveryCriteria(query="Ports in GCC")) == {
        "query": "Ports in GCC"
    }


def test_suggest_criteria_derives_filters_from_active_icp(session):
    seeded = seed(session)
    suggestion = service(session, seeded, FakeDiscovery([])).suggest_criteria(
        seeded["product"].id
    )
    assert suggestion["countries"] == ["AE", "SA"]
    assert suggestion["keywords"] == ["Logistics"]
    assert suggestion["headcounts"] == ["51-200", "201-500", "501-1000", "1001-5000"]
    assert suggestion["icp_id"] == seeded["icp"].id


def test_preview_flags_known_companies_and_add_is_idempotent(session):
    seeded = seed(session)
    attach_existing(session, seeded, "known.example")
    provider = FakeDiscovery([("known.example", "Known"), ("new.example", "New Co")])
    svc = service(session, seeded, provider)

    preview = svc.preview(normalize_criteria(countries=["SA"]))
    flags = {item["domain"]: item["already_in_list"] for item in preview["companies"]}
    assert flags == {"known.example": True, "new.example": False}

    result = svc.add([{"domain": "new.example", "name": "New Co"}])
    assert [item["domain"] for item in result["added"]] == ["new.example"]
    again = svc.add([{"domain": "new.example", "name": "New Co"}, {"domain": "bad domain"}])
    assert again["added"] == []
    assert again["already_known"] == 1
    assert again["invalid"] == 1
    relationship = session.scalar(
        select(OrganizationCompany)
        .join(CompanyIdentifier, CompanyIdentifier.company_id == OrganizationCompany.company_id)
        .where(CompanyIdentifier.normalized_value == "new.example")
    )
    assert relationship.lifecycle_status == "discovered"


def test_read_only_users_cannot_discover_or_manage_rules(session):
    seeded = seed(session, role=MembershipRole.READ_ONLY)
    svc = service(session, seeded, FakeDiscovery([]), role=MembershipRole.READ_ONLY)
    with pytest.raises(AuthorizationError):
        svc.preview(normalize_criteria(countries=["SA"]))
    with pytest.raises(AuthorizationError):
        svc.create({"product_id": seeded["product"].id, "countries": ["SA"]})


def test_scheduled_run_adds_only_new_companies_up_to_limit_and_advances(session):
    seeded = seed(session)
    attach_existing(session, seeded, "c1.example")
    provider = FakeDiscovery(
        [(f"c{i}.example", f"Company {i}") for i in range(1, 6)], total=5
    )
    svc = service(session, seeded, provider)
    policy = svc.create(
        {
            "product_id": seeded["product"].id,
            "countries": ["SA"],
            "max_new_per_run": 2,
            "cadence_minutes": 1440,
            "run_workflow": False,
        }
    )
    session.commit()

    executor = CompanyDiscoveryExecutor(session, provider, ProviderRegistry([]), now=NOW)
    runs = executor.run_due()
    assert len(runs) == 1
    run = runs[0]
    assert run.status == CompanyDiscoveryRunStatus.COMPLETED
    assert run.candidates_found == 5
    assert run.companies_added == 2
    assert run.already_known == 1
    assert policy.next_run_at == NOW + timedelta(days=1)
    assert executor.run_due() == []

    later = CompanyDiscoveryExecutor(
        session, provider, ProviderRegistry([]), now=NOW + timedelta(days=1)
    ).run_due()
    assert later[0].companies_added == 2
    last = CompanyDiscoveryExecutor(
        session, provider, ProviderRegistry([]), now=NOW + timedelta(days=2)
    ).run_due()
    assert last[0].companies_added == 0
    assert any("No new companies" in note for note in last[0].notes)
    total = session.scalar(
        select(func.count())
        .select_from(OrganizationCompany)
        .where(OrganizationCompany.organization_id == seeded["org"].id)
    )
    assert total == 5


def test_run_now_with_workflow_records_outcomes(session):
    seeded = seed(session)
    provider = FakeDiscovery([("w1.example", "W1")], total=1)
    svc = service(session, seeded, provider)
    policy = svc.create(
        {"product_id": seeded["product"].id, "keywords": ["ports"], "run_workflow": True}
    )
    run = svc.run_now(policy.id)
    assert run.status == CompanyDiscoveryRunStatus.COMPLETED
    assert run.companies_added == 1
    assert run.workflows_completed + run.workflows_failed == 1


def test_run_fails_safely_when_run_as_user_loses_permission(session):
    seeded = seed(session)
    provider = FakeDiscovery([("x.example", "X")])
    policy = service(session, seeded, provider).create(
        {"product_id": seeded["product"].id, "countries": ["SA"], "run_workflow": False}
    )
    membership = session.scalar(select(OrganizationMembership))
    membership.role = MembershipRole.READ_ONLY
    session.commit()

    runs = CompanyDiscoveryExecutor(session, provider, ProviderRegistry([]), now=NOW).run_due()
    assert runs[0].status == CompanyDiscoveryRunStatus.FAILED
    assert runs[0].error_code == "DISCOVERY_RUN_AS_PERMISSION_DENIED"
    assert policy.last_error_code == "DISCOVERY_RUN_AS_PERMISSION_DENIED"
    assert provider.calls == []


def test_invalid_industry_falls_back_to_keywords_and_pagination_errors_reset(session):
    seeded = seed(session)
    provider = FakeDiscovery(
        [("i1.example", "I1")],
        errors=[DiscoveryInvalidFilter("bad industry", "invalid_industry")],
    )
    svc = service(session, seeded, provider)
    preview = svc.preview(normalize_criteria(industries=["Ports & Harbours"]))
    assert preview["companies"][0]["domain"] == "i1.example"
    assert provider.calls[-1][0].keywords == ("Ports & Harbours",)
    assert provider.calls[-1][0].industries == ()
    assert preview["notes"]

    policy = svc.create(
        {"product_id": seeded["product"].id, "countries": ["SA"], "run_workflow": False}
    )
    policy.next_offset = 100
    provider.errors = [DiscoveryPaginationUnavailable("free plan")]
    run = svc.run_now(policy.id)
    assert run.status == CompanyDiscoveryRunStatus.COMPLETED
    assert policy.next_offset == 0
    assert provider.calls[-1][1] == 0


def test_unconfigured_provider_fails_run_with_clear_code(session):
    seeded = seed(session)
    from marketingiq.domain.providers import ProviderNotConfigured

    provider = FakeDiscovery([], errors=[ProviderNotConfigured("no key")])
    svc = service(session, seeded, provider)
    policy = svc.create({"product_id": seeded["product"].id, "countries": ["SA"]})
    run = svc.run_now(policy.id)
    assert run.status == CompanyDiscoveryRunStatus.FAILED
    assert run.error_code == "DISCOVERY_PROVIDER_NOT_CONFIGURED"


def _hunter(handler):
    return HunterDiscoveryProvider(
        api_key="test-key", client=httpx.Client(transport=httpx.MockTransport(handler))
    )


def test_hunter_provider_parses_results_and_maps_errors():
    seen = {}

    def ok(request):
        seen["body"] = json.loads(request.content)
        seen["key"] = request.headers.get("x-api-key")
        return httpx.Response(
            200,
            json={
                "data": [
                    {"domain": "Acme.example", "organization": "Acme"},
                    {"domain": "", "organization": "No domain"},
                    "junk",
                ],
                "meta": {"results": 42},
            },
        )

    result = _hunter(ok).discover(DiscoveryCriteria(countries=("SA",)))
    assert [(c.domain, c.name) for c in result.companies] == [("acme.example", "Acme")]
    assert result.total_results == 42
    assert seen["key"] == "test-key"
    assert "limit" not in seen["body"]

    _hunter(ok).discover(DiscoveryCriteria(countries=("SA",)), offset=100)
    assert seen["body"]["offset"] == 100

    def error(code, status=400):
        return lambda request: httpx.Response(status, json={"errors": [{"id": code}]})

    with pytest.raises(DiscoveryPaginationUnavailable):
        _hunter(error("pagination_error")).discover(DiscoveryCriteria(countries=("SA",)))
    with pytest.raises(DiscoveryAccessDenied):
        _hunter(error("no_discover_access", 403)).discover(DiscoveryCriteria(countries=("SA",)))
    with pytest.raises(DiscoveryInvalidFilter) as invalid:
        _hunter(error("invalid_industry")).discover(DiscoveryCriteria(industries=("x",)))
    assert invalid.value.code == "invalid_industry"


def test_discovery_api_end_to_end(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'discovery.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        seeded = seed(session)
        org_id, product_id = seeded["org"].id, seeded["product"].id
    app = create_app(url, SECRET)
    app.state.discovery_provider = FakeDiscovery(
        [("api1.example", "API One"), ("api2.example", "API Two")], total=2
    )
    client = TestClient(app)
    token = client.post(
        "/api/v1/auth/login", json={"email": "owner@example.test", "password": PASSWORD}
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    base = f"/api/v1/organizations/{org_id}/company-discovery"

    suggestion = client.get(base + "/suggest", params={"product_id": product_id}, headers=headers)
    assert suggestion.status_code == 200
    assert suggestion.json()["countries"] == ["AE", "SA"]

    preview = client.post(base + "/preview", json={"countries": ["SA"]}, headers=headers)
    assert preview.status_code == 200
    assert len(preview.json()["companies"]) == 2
    assert client.post(base + "/preview", json={}, headers=headers).status_code == 422

    added = client.post(
        base + "/add", json={"companies": [{"domain": "api1.example"}]}, headers=headers
    )
    assert added.status_code == 201
    assert added.json()["added"][0]["domain"] == "api1.example"

    created = client.post(
        base + "/policies",
        json={"product_id": product_id, "countries": ["SA"], "run_workflow": False},
        headers=headers,
    )
    assert created.status_code == 201
    policy_id = created.json()["id"]
    run = client.post(base + f"/policies/{policy_id}/run", headers=headers)
    assert run.status_code == 201
    assert run.json()["companies_added"] == 1
    runs = client.get(base + f"/policies/{policy_id}/runs", headers=headers).json()
    assert len(runs) == 1
    updated = client.patch(
        base + f"/policies/{policy_id}", json={"enabled": False}, headers=headers
    )
    assert updated.json()["enabled"] is False
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(CompanyDiscoveryRun)) == 1


class FakeAISource:
    key = "AI_WEB_SEARCH"
    label = "AI web search"
    accepts_exclusions = True
    costs_credits = True
    configured = True

    def status(self):
        return {"provider": "deepseek", "model": "deepseek-v4-flash", "configuration_error": None}

    def discover(self, criteria, offset=0, exclude_domains=()):
        return DiscoveryResult(
            "AI_WEB_SEARCH",
            (
                DiscoveredCompany(
                    "aiport.example",
                    "AI Port",
                    source_urls=("https://aiport.example/about",),
                    rationale="Runs a container terminal.",
                ),
            ),
            None,
            usage={"provider": "deepseek", "searches": 3},
        )


def test_discovery_api_ai_source_selection_and_daily_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("DISCOVERY_AI_DAILY_LIMIT", "1")
    url = f"sqlite+pysqlite:///{tmp_path / 'discovery-ai.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        seeded = seed(session)
        org_id, product_id = seeded["org"].id, seeded["product"].id
    app = create_app(url, SECRET)
    app.state.discovery_provider = FakeDiscovery([("hunter1.example", "Hunter One")], total=1)
    app.state.extra_discovery_providers = {"AI_WEB_SEARCH": FakeAISource()}
    client = TestClient(app)
    token = client.post(
        "/api/v1/auth/login", json={"email": "owner@example.test", "password": PASSWORD}
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    base = f"/api/v1/organizations/{org_id}/company-discovery"

    sources = {s["key"]: s for s in client.get(base + "/sources", headers=headers).json()}
    assert sources["AI_WEB_SEARCH"]["used_today"] == 0
    assert sources["AI_WEB_SEARCH"]["daily_limit"] == 1
    assert "api_key" not in str(sources)

    preview = client.post(
        base + "/preview", json={"countries": ["SA"], "provider_key": "AI_WEB_SEARCH"},
        headers=headers,
    )
    assert preview.status_code == 200
    company = preview.json()["companies"][0]
    assert company["source_urls"] == ["https://aiport.example/about"]
    assert company["rationale"] == "Runs a container terminal."

    limited = client.post(
        base + "/preview", json={"countries": ["SA"], "provider_key": "AI_WEB_SEARCH"},
        headers=headers,
    )
    assert limited.status_code == 409
    assert limited.json()["code"] == "DAILY_LIMIT"
    unknown = client.post(
        base + "/preview", json={"countries": ["SA"], "provider_key": "BING"}, headers=headers
    )
    assert unknown.status_code == 422

    created = client.post(
        base + "/policies",
        json={"product_id": product_id, "countries": ["SA"], "run_workflow": False,
              "provider_key": "AI_WEB_SEARCH"},
        headers=headers,
    )
    assert created.status_code == 201
    assert created.json()["provider_key"] == "AI_WEB_SEARCH"
    run = client.post(base + f"/policies/{created.json()['id']}/run", headers=headers).json()
    assert run["status"] == "FAILED" and run["error_code"] == "DISCOVERY_DAILY_LIMIT"
