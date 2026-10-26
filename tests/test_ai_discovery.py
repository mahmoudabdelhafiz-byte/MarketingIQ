"""AI web search as a company discovery source (service rules and provider adapters)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import marketingiq.domain.campaigns  # noqa: F401  (registers tables referenced by other models)
from marketingiq.application.discovery import (
    AI_SEARCH_ACTION,
    CompanyDiscoveryExecutor,
    CompanyDiscoveryService,
    DiscoveryDailyLimitReached,
    normalize_criteria,
)
from marketingiq.application.errors import AuthorizationError
from marketingiq.application.research import ProviderRegistry
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.discovery import (
    CompanyDiscoveryPolicy,
    CompanyDiscoveryRunStatus,
    DiscoveredCompany,
    DiscoveryCriteria,
    DiscoveryResult,
)
from marketingiq.domain.models import (
    AuditLog,
    Company,
    CompanyIdentifier,
    MembershipRole,
    Organization,
    OrganizationCompany,
    OrganizationMembership,
    Product,
    User,
)
from marketingiq.domain.providers import ProviderNotConfigured
from marketingiq.infrastructure.ai_discovery import (
    KEY,
    AIDiscoveryError,
    AIWebSearchDiscoveryProvider,
    build_ai_discovery_provider,
    normalize_source_url,
)

NOW = datetime(2026, 10, 26, 9, 0, tzinfo=UTC)
CRITERIA = DiscoveryCriteria(countries=("SA",), keywords=("container terminal",))


class FakeHunter:
    key = "HUNTER"
    configured = True

    def __init__(self):
        self.calls = []

    def discover(self, criteria, offset=0):
        self.calls.append((criteria, offset))
        return DiscoveryResult("HUNTER", (DiscoveredCompany("hunter.example", "Hunter Co"),), 1)


class FakeAI:
    key = KEY
    label = "AI web search"
    accepts_exclusions = True
    costs_credits = True

    def __init__(self, companies=None, configured=True):
        self.companies = companies or [
            DiscoveredCompany(
                "portalpha.example",
                "Port Alpha",
                source_urls=("https://portalpha.example/about",),
                rationale="Operates a container terminal in Jeddah.",
            ),
            DiscoveredCompany("existing.example", "Existing Terminal"),
        ]
        self.configured = configured
        self.calls = []

    def status(self):
        return {"provider": "anthropic", "model": "claude-test", "configuration_error": None}

    def discover(self, criteria, offset=0, exclude_domains=()):
        self.calls.append((criteria, offset, exclude_domains))
        return DiscoveryResult(
            KEY,
            tuple(self.companies),
            None,
            notes=("AI search: 1 suggestion(s) dropped (no retrieved page supported them).",),
            usage={"provider": "anthropic", "model": "claude-test", "searches": 4,
                   "input_tokens": 900, "output_tokens": 200},
        )


def seed(session: Session, role=MembershipRole.ORGANIZATION_ADMIN):
    org = Organization(name="Barmageyat", slug="barmageyat-ai")
    user = User(email="ai-owner@example.test", password_hash="x")
    session.add_all([org, user])
    session.flush()
    session.add(OrganizationMembership(organization_id=org.id, user_id=user.id, role=role))
    product = Product(organization_id=org.id, name="TAS", slug="tas", value_proposition="Gates")
    existing = Company(canonical_name="Existing Terminal")
    existing.identifiers.append(
        CompanyIdentifier(
            kind="DOMAIN", value="existing.example", normalized_value="existing.example",
            is_primary=True,
        )
    )
    session.add_all([product, existing])
    session.flush()
    session.add(OrganizationCompany(organization_id=org.id, company=existing))
    session.commit()
    return {"org": org, "user": user, "product": product}


def service(session, seeded, hunter, ai, role=MembershipRole.ORGANIZATION_ADMIN, now=NOW):
    return CompanyDiscoveryService(
        session,
        TenantContext(seeded["org"].id, seeded["user"].id, role),
        hunter,
        ProviderRegistry([]),
        now=now,
        extra_providers={KEY: ai},
    )


def spend_rows(session):
    return session.scalars(select(AuditLog).where(AuditLog.action == AI_SEARCH_ACTION)).all()


def test_sources_list_hunter_and_ai_with_budget(session, monkeypatch):
    monkeypatch.setenv("DISCOVERY_AI_DAILY_LIMIT", "3")
    seeded = seed(session)
    sources = {s["key"]: s for s in service(session, seeded, FakeHunter(), FakeAI()).sources()}
    assert sources["HUNTER"]["costs_credits"] is False and "daily_limit" not in sources["HUNTER"]
    ai = sources[KEY]
    assert (ai["label"], ai["provider"]) == ("AI web search", "anthropic")
    assert ai["model"] == "claude-test"
    assert (ai["daily_limit"], ai["used_today"]) == (3, 0)


def test_ai_preview_passes_exclusions_returns_evidence_and_records_spend(session):
    seeded = seed(session)
    hunter, ai = FakeHunter(), FakeAI()
    preview = service(session, seeded, hunter, ai).preview(CRITERIA, provider_key="ai_web_search")

    assert hunter.calls == []
    assert ai.calls[0][2] == ("existing.example",)
    assert preview["provider_key"] == KEY
    alpha, existing = preview["companies"]
    assert alpha["source_urls"] == ["https://portalpha.example/about"]
    assert alpha["rationale"].startswith("Operates")
    assert existing["already_in_list"] is True
    assert "dropped" in preview["notes"][0]
    [row] = spend_rows(session)
    assert row.metadata_json["context"] == "preview"
    assert row.metadata_json["status"] == "completed"
    assert row.metadata_json["searches"] == 4 and row.metadata_json["model"] == "claude-test"
    assert "portalpha" not in json.dumps(row.metadata_json)

    hunter_preview = service(session, seeded, hunter, ai).preview(CRITERIA)
    assert hunter_preview["provider_key"] == "HUNTER"
    assert hunter_preview["companies"][0]["source_urls"] == []
    assert len(spend_rows(session)) == 1  # Hunter Discover is free and not counted


def test_daily_ai_limit_blocks_further_searches_until_tomorrow(session, monkeypatch):
    monkeypatch.setenv("DISCOVERY_AI_DAILY_LIMIT", "1")
    seeded = seed(session)
    ai = FakeAI()
    service(session, seeded, FakeHunter(), ai).preview(CRITERIA, provider_key=KEY)
    with pytest.raises(DiscoveryDailyLimitReached):
        service(session, seeded, FakeHunter(), ai).preview(CRITERIA, provider_key=KEY)
    assert len(ai.calls) == 1
    tomorrow = NOW + timedelta(days=1)
    service(session, seeded, FakeHunter(), ai, now=tomorrow).preview(CRITERIA, provider_key=KEY)
    assert len(ai.calls) == 2


def test_unknown_source_and_permissions(session):
    seeded = seed(session)
    with pytest.raises(ValueError, match="Unknown discovery source"):
        service(session, seeded, FakeHunter(), FakeAI()).preview(CRITERIA, provider_key="GOOGLE")
    reader = service(session, seeded, FakeHunter(), FakeAI(), role=MembershipRole.READ_ONLY)
    with pytest.raises(AuthorizationError):
        reader.preview(CRITERIA, provider_key=KEY)
    assert reader.sources()  # reading the source list is allowed


def test_rule_with_ai_source_runs_through_ai_and_records_spend(session):
    seeded = seed(session)
    hunter, ai = FakeHunter(), FakeAI()
    svc = service(session, seeded, hunter, ai)
    policy = svc.create(
        {
            "product_id": seeded["product"].id,
            "provider_key": KEY,
            "countries": ["SA"],
            "keywords": ["container terminal"],
            "run_workflow": False,
        }
    )
    assert policy.provider_key == KEY

    run = svc.run_now(policy.id)

    assert run.status == CompanyDiscoveryRunStatus.COMPLETED
    assert (run.candidates_found, run.companies_added, run.already_known) == (2, 1, 1)
    assert hunter.calls == [] and len(ai.calls) == 1
    assert [r.metadata_json["context"] for r in spend_rows(session)] == ["rule"]

    svc.update(policy.id, {"provider_key": "HUNTER"})
    assert session.get(CompanyDiscoveryPolicy, policy.id).provider_key == "HUNTER"


def test_scheduled_ai_rule_respects_budget_and_unknown_sources(session, monkeypatch):
    monkeypatch.setenv("DISCOVERY_AI_DAILY_LIMIT", "1")
    seeded = seed(session)
    ai = FakeAI()
    svc = service(session, seeded, FakeHunter(), ai)
    criteria = normalize_criteria(countries=["SA"])
    policy = svc.create(
        {"product_id": seeded["product"].id, "provider_key": KEY, "run_workflow": False,
         **criteria.as_dict()}
    )
    svc.preview(CRITERIA, provider_key=KEY)  # uses today's only AI search

    executor = CompanyDiscoveryExecutor(
        session, FakeHunter(), ProviderRegistry([]), now=NOW, extra_providers={KEY: ai}
    )
    [run] = executor.run_due()
    assert run.status == CompanyDiscoveryRunStatus.FAILED
    assert run.error_code == "DISCOVERY_DAILY_LIMIT"

    policy.provider_key = "SOMETHING_ELSE"
    policy.next_run_at = NOW + timedelta(minutes=1)
    session.flush()
    [run] = CompanyDiscoveryExecutor(
        session, FakeHunter(), ProviderRegistry([]), now=NOW + timedelta(minutes=5)
    ).run_due()
    assert run.error_code == "DISCOVERY_UNKNOWN_SOURCE"
    assert session.scalar(select(func.count(AuditLog.id)).where(
        AuditLog.action == AI_SEARCH_ACTION)) == 1


def test_unconfigured_ai_rule_fails_cleanly(session):
    seeded = seed(session)
    ai = FakeAI(configured=False)

    def not_configured(*_args, **_kwargs):
        raise ProviderNotConfigured("not configured")

    ai.discover = not_configured
    svc = service(session, seeded, FakeHunter(), ai)
    policy = svc.create(
        {"product_id": seeded["product"].id, "provider_key": KEY, "countries": ["SA"],
         "run_workflow": False}
    )
    run = svc.run_now(policy.id)
    assert run.error_code == "DISCOVERY_PROVIDER_NOT_CONFIGURED"


# ------------------------------------------------------------------------------ adapters

COMPANIES = {
    "companies": [
        {"name": "Port Alpha", "domain": "portalpha.example", "rationale": "Runs a terminal.",
         "source_urls": ["https://www.portalpha.example/about/"]},
        {"name": "Ghost Port", "domain": "ghost.example", "rationale": "Made up.",
         "source_urls": ["https://never-retrieved.example/"]},
    ]
}


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_openai_adapter_grounds_on_consulted_sources(monkeypatch):
    monkeypatch.setenv("DISCOVERY_MAX_SEARCHES", "5")
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={
            "status": "completed",
            "output": [
                {"type": "web_search_call", "action": {"type": "search", "sources": [
                    {"type": "url", "url": "https://portalpha.example/about"}]}},
                {"type": "message", "content": [{
                    "type": "output_text",
                    "text": f"Done <result>{json.dumps(COMPANIES)}</result>",
                    "annotations": []}]},
            ],
            "usage": {"input_tokens": 3000, "output_tokens": 400},
        })

    provider = build_ai_discovery_provider({"OPENAI_API_KEY": "o"}, client(handler))
    result = provider.discover(CRITERIA, exclude_domains=("existing.example",))

    body = seen[0]
    assert body["model"] == "gpt-5.5" and body["tools"] == [{"type": "web_search"}]
    assert body["include"] == ["web_search_call.action.sources"] and body["max_tool_calls"] == 5
    assert "existing.example" in body["input"] and "container terminal" in body["input"]
    assert [c.domain for c in result.companies] == ["portalpha.example"]
    assert result.companies[0].source_urls == ("https://www.portalpha.example/about/",)
    assert "1 suggestion(s) dropped" in result.notes[0]
    assert result.usage["searches"] == 1 and result.usage["input_tokens"] == 3000


def test_anthropic_adapter_resumes_pause_turn():
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            return httpx.Response(200, json={
                "content": [{"type": "web_search_tool_result", "tool_use_id": "s1", "content": [
                    {"type": "web_search_result", "url": "https://portalpha.example/about"}]}],
                "stop_reason": "pause_turn",
                "usage": {"input_tokens": 10, "output_tokens": 2,
                          "server_tool_use": {"web_search_requests": 2}},
            })
        return httpx.Response(200, json={
            "content": [{"type": "text", "text": f"<result>{json.dumps(COMPANIES)}</result>"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 5, "output_tokens": 3},
        })

    provider = build_ai_discovery_provider(
        {"AI_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "a"}, client(handler)
    )
    result = provider.discover(CRITERIA)
    assert calls[0]["tools"][0]["type"] == "web_search_20250305"
    assert calls[1]["messages"][1]["role"] == "assistant"
    assert [c.domain for c in result.companies] == ["portalpha.example"]
    assert (result.usage["searches"], result.usage["input_tokens"]) == (2, 15)


def test_deepseek_adapter_plans_searches_and_selects():
    log = []

    def handler(request):
        log.append(request.url.path)
        body = json.loads(request.content)
        if request.url.path == "/chat/completions":
            prompt = body["messages"][-1]["content"]
            content = (json.dumps({"queries": ["terminal operators Saudi Arabia", "ports"]})
                       if "Plan up to" in prompt else json.dumps(COMPANIES))
            return httpx.Response(200, json={
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50}})
        if body["query"] == "ports":
            return httpx.Response(504)
        return httpx.Response(200, json={"results": [
            {"title": "About", "url": "https://portalpha.example/about", "summary": "Terminal."}]})

    provider = build_ai_discovery_provider(
        {"AI_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": "d"}, client(handler)
    )
    result = provider.discover(CRITERIA)
    assert log == ["/chat/completions", "/v1/tools/search", "/v1/tools/search",
                   "/chat/completions"]
    assert [c.domain for c in result.companies] == ["portalpha.example"]
    assert result.usage["searches"] == 1 and result.usage["output_tokens"] == 100


@pytest.mark.parametrize(
    ("status", "category"),
    [(402, "INSUFFICIENT_BALANCE"), (429, "RATE_LIMITED"), (401, "AUTHENTICATION_ERROR")],
)
def test_adapter_errors_are_safe_categories(status, category):
    provider = build_ai_discovery_provider(
        {"OPENAI_API_KEY": "o"}, client(lambda _r: httpx.Response(status, json={"x": "secret"}))
    )
    with pytest.raises(AIDiscoveryError) as error:
        provider.discover(CRITERIA)
    assert error.value.category == category and "secret" not in str(error.value)


def test_adapter_requires_configuration_and_filters():
    provider = build_ai_discovery_provider({"AI_PROVIDER": "anthropic"})
    assert provider.configured is False and provider.status()["configured"] is False
    with pytest.raises(ProviderNotConfigured):
        provider.discover(CRITERIA)
    configured = AIWebSearchDiscoveryProvider(
        build_ai_discovery_provider({"OPENAI_API_KEY": "o"}).settings
    )
    with pytest.raises(ValueError):
        configured.discover(DiscoveryCriteria())


def test_normalize_source_url():
    assert normalize_source_url("http://WWW.Example.com/a/") == "https://example.com/a"
    assert normalize_source_url("ftp://example.com") is None
    assert normalize_source_url("https://u:p@example.com/") is None


def test_failed_ai_search_still_counts_toward_the_daily_limit(session, monkeypatch):
    monkeypatch.setenv("DISCOVERY_AI_DAILY_LIMIT", "1")
    seeded = seed(session)
    ai = FakeAI()

    def failing(*_args, **_kwargs):
        raise AIDiscoveryError("MALFORMED_RESPONSE")

    ai.discover = failing
    with pytest.raises(AIDiscoveryError):
        service(session, seeded, FakeHunter(), ai).preview(CRITERIA, provider_key=KEY)
    [row] = spend_rows(session)
    assert row.metadata_json["status"] == "started"
    with pytest.raises(DiscoveryDailyLimitReached):
        service(session, seeded, FakeHunter(), ai).preview(CRITERIA, provider_key=KEY)


def test_unconfigured_ai_source_does_not_use_budget(session):
    seeded = seed(session)
    with pytest.raises(ProviderNotConfigured):
        service(session, seeded, FakeHunter(), FakeAI(configured=False)).preview(
            CRITERIA, provider_key=KEY
        )
    assert spend_rows(session) == []
