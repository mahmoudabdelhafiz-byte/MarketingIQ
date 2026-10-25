import json

import httpx
import pytest

from marketingiq.domain.discovery import (
    DiscoveryCriterion,
    DiscoveryProviderError,
    DiscoveryRequest,
)
from marketingiq.infrastructure.discovery_provider import (
    ANTHROPIC_VERSION,
    MESSAGES_URL,
    WEB_SEARCH_TOOL,
    AnthropicCompanyDiscoveryProvider,
    build_prompt,
    parse_companies,
)

REQUEST = DiscoveryRequest(
    product_name="Truck Appointment System",
    product_description="Gate booking",
    value_proposition="Cut gate queues.",
    criteria=(DiscoveryCriterion("industry", "Container terminal", True),),
    employee_min=200,
    employee_max=None,
    exclude_domains=("existing.example",),
    max_results=5,
)

RESULT_JSON = {
    "companies": [
        {
            "name": "Port Alpha",
            "domain": "portalpha.example",
            "country_code": "SA",
            "industry": "Container terminal",
            "rationale": "Operates a container terminal.",
            "source_urls": ["https://portalpha.example/about"],
        },
        {"name": "No domain"},
        "junk",
    ]
}


def search_turn(*, stop_reason="end_turn", text=None, searches=2):
    content = [
        {"type": "text", "text": "Searching."},
        {
            "type": "server_tool_use",
            "id": "srvtoolu_1",
            "name": "web_search",
            "input": {"query": "container terminal operators Saudi Arabia"},
        },
        {
            "type": "web_search_tool_result",
            "tool_use_id": "srvtoolu_1",
            "content": [
                {
                    "type": "web_search_result",
                    "url": "https://portalpha.example/about",
                    "title": "About Port Alpha",
                    "encrypted_content": "abc",
                }
            ],
        },
    ]
    if text is not None:
        content.append(
            {
                "type": "text",
                "text": text,
                "citations": [
                    {
                        "type": "web_search_result_location",
                        "url": "https://news.example/port-alpha",
                        "cited_text": "Port Alpha expands",
                    }
                ],
            }
        )
    return {
        "content": content,
        "stop_reason": stop_reason,
        "usage": {
            "input_tokens": 1000,
            "output_tokens": 200,
            "server_tool_use": {"web_search_requests": searches},
        },
    }


def provider_with(handler, monkeypatch, key="test-key"):
    monkeypatch.setenv("ANTHROPIC_API_KEY", key)
    monkeypatch.setenv("DISCOVERY_AI_MODEL", "claude-test")
    monkeypatch.setenv("DISCOVERY_MAX_SEARCHES", "4")
    return AnthropicCompanyDiscoveryProvider(httpx.Client(transport=httpx.MockTransport(handler)))


def test_discovery_parses_grounded_candidates(monkeypatch):
    seen = []

    def handler(request: httpx.Request):
        seen.append(request)
        return httpx.Response(
            200, json=search_turn(text="Done.\n<result>" + json.dumps(RESULT_JSON) + "</result>")
        )

    provider = provider_with(handler, monkeypatch)
    result = provider.discover(REQUEST)

    request = seen[0]
    assert str(request.url) == MESSAGES_URL
    assert request.headers["x-api-key"] == "test-key"
    assert request.headers["anthropic-version"] == ANTHROPIC_VERSION
    body = json.loads(request.content)
    assert body["model"] == "claude-test"
    assert body["tools"] == [{"type": WEB_SEARCH_TOOL, "name": "web_search", "max_uses": 4}]
    assert "existing.example" in body["messages"][0]["content"]

    assert [c.domain for c in result.companies] == ["portalpha.example"]
    assert result.companies[0].source_urls == ("https://portalpha.example/about",)
    assert result.retrieved_urls == {
        "https://portalpha.example/about",
        "https://news.example/port-alpha",
    }
    assert (result.searches_used, result.input_tokens, result.output_tokens) == (2, 1000, 200)


def test_pause_turn_is_resumed_with_assistant_content(monkeypatch):
    calls = []

    def handler(request: httpx.Request):
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return httpx.Response(200, json=search_turn(stop_reason="pause_turn", searches=3))
        return httpx.Response(
            200,
            json={
                "content": [
                    {"type": "text", "text": "<result>" + json.dumps(RESULT_JSON) + "</result>"}
                ],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 500, "output_tokens": 100},
            },
        )

    result = provider_with(handler, monkeypatch).discover(REQUEST)

    assert len(calls) == 2
    assert calls[1]["messages"][1]["role"] == "assistant"
    assert calls[1]["messages"][1]["content"] == search_turn(stop_reason="pause_turn")["content"]
    assert result.searches_used == 3
    assert result.input_tokens == 1500
    assert "https://portalpha.example/about" in result.retrieved_urls


def test_endless_pause_turn_is_incomplete(monkeypatch):
    def handler(_request):
        return httpx.Response(200, json=search_turn(stop_reason="pause_turn"))

    with pytest.raises(DiscoveryProviderError) as error:
        provider_with(handler, monkeypatch).discover(REQUEST)
    assert error.value.category == "INCOMPLETE_RESPONSE"


@pytest.mark.parametrize(
    ("status", "category"),
    [
        (429, "RATE_LIMITED"),
        (401, "AUTHENTICATION_ERROR"),
        (529, "PROVIDER_UNAVAILABLE"),
        (400, "PROVIDER_REJECTED_REQUEST"),
    ],
)
def test_http_errors_map_to_safe_categories(monkeypatch, status, category):
    def handler(_request):
        return httpx.Response(status, json={"error": {"message": "secret detail"}})

    with pytest.raises(DiscoveryProviderError) as error:
        provider_with(handler, monkeypatch).discover(REQUEST)
    assert error.value.category == category
    assert "secret" not in str(error.value)


def test_network_error_and_missing_result_block(monkeypatch):
    def broken(_request):
        raise httpx.ConnectError("boom")

    with pytest.raises(DiscoveryProviderError) as error:
        provider_with(broken, monkeypatch).discover(REQUEST)
    assert error.value.category == "NETWORK_ERROR"

    def no_result(_request):
        return httpx.Response(200, json=search_turn(text="I could not find anything."))

    with pytest.raises(DiscoveryProviderError) as error:
        provider_with(no_result, monkeypatch).discover(REQUEST)
    assert error.value.category == "MALFORMED_RESPONSE"


def test_not_configured_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    provider = AnthropicCompanyDiscoveryProvider()
    assert provider.configured is False
    assert provider.status()["configured"] is False
    with pytest.raises(DiscoveryProviderError) as error:
        provider.discover(REQUEST)
    assert error.value.category == "NOT_CONFIGURED"


def test_parse_companies_accepts_fenced_json_and_uses_last_block():
    text = (
        '<result>{"companies": []}</result> then '
        "<result>```json\n" + json.dumps(RESULT_JSON) + "\n```</result>"
    )
    assert [c.name for c in parse_companies(text)] == ["Port Alpha"]
    with pytest.raises(DiscoveryProviderError):
        parse_companies('<result>{"companies": "nope"}</result>')


def test_prompt_contains_profile_and_untrusted_content_guard():
    prompt = json.loads(build_prompt(REQUEST))
    assert prompt["target_profile"]["ideal_customer_criteria"] == [
        {"kind": "industry", "value": "Container terminal", "required": True}
    ]
    assert prompt["exclude_domains"] == ["existing.example"]
    assert "<result>" in prompt["output_format"]
