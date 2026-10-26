import json

import httpx
import pytest

import marketingiq.domain.campaigns  # noqa: F401  (registers tables campaign_ai references)
from marketingiq.domain.campaign_ai import (
    CampaignAIProviderError,
    CampaignAIRequest,
    GroundingFact,
)
from marketingiq.infrastructure.ai_config import (
    AIFeature,
    AIProviderName,
    model_family,
    resolve_ai_settings,
)
from marketingiq.infrastructure.campaign_ai_provider import (
    OpenAICampaignAIProvider,
    build_campaign_ai_provider,
)
from marketingiq.infrastructure.decision_maker_ai import (
    DecisionMakerAIError,
    build_decision_maker_ai_client,
)

CAMPAIGN_REQUEST = CampaignAIRequest(
    company_name="Port Alpha",
    recipient_first_name="Sara",
    buyer_role="Terminal Operations Manager",
    product_name="Truck Appointment System",
    value_proposition="Cut gate queues with booked truck slots.",
    call_to_action="Open to a 20-minute call?",
    grounding_facts=(GroundingFact("f1", "industry", "Container terminal"),),
)
DRAFT_JSON = json.dumps(
    {"subject": "Gate queues at Port Alpha", "body": "Hi Sara, ...", "used_fact_keys": ["industry"]}
)
def mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


# ------------------------------------------------------------------------------ configuration


def test_default_provider_is_openai_for_backwards_compatibility():
    settings = resolve_ai_settings(AIFeature.CAMPAIGN, {"OPENAI_API_KEY": "k"})
    assert settings.provider == AIProviderName.OPENAI
    assert settings.model == "gpt-5.6-luna"
    assert settings.timeout_seconds == 30
    assert settings.configured is True


def test_global_switch_and_per_feature_override():
    env = {
        "AI_PROVIDER": "Anthropic",
        "DISCOVERY_AI_PROVIDER": "deepseek",
        "ANTHROPIC_API_KEY": "a",
        "DEEPSEEK_API_KEY": "d",
        "DEEPSEEK_BASE_URL": "https://proxy.example/",
    }
    campaign = resolve_ai_settings(AIFeature.CAMPAIGN, env)
    discovery = resolve_ai_settings(AIFeature.DISCOVERY, env)
    assert (campaign.provider, campaign.model) == (AIProviderName.ANTHROPIC, "claude-sonnet-5-5")
    assert (discovery.provider, discovery.model) == (AIProviderName.DEEPSEEK, "deepseek-v4-flash")
    assert discovery.base_url == "https://proxy.example"
    assert discovery.timeout_seconds == 180


def test_model_from_another_vendor_disables_the_feature_with_a_reason():
    settings = resolve_ai_settings(
        AIFeature.CAMPAIGN,
        {
            "AI_PROVIDER": "deepseek",
            "DEEPSEEK_API_KEY": "secret-ds-key",
            "CAMPAIGN_AI_MODEL": "gpt-5.6-luna",
        },
    )
    assert settings.configured is False
    assert "looks like a openai model" in settings.error
    assert settings.status()["configuration_error"] == settings.error
    assert "secret-ds-key" not in str(settings.status())


def test_unknown_provider_is_reported():
    settings = resolve_ai_settings(AIFeature.DISCOVERY, {"AI_PROVIDER": "gemini"})
    assert settings.provider is None and settings.configured is False
    assert "gemini" in settings.error


@pytest.mark.parametrize(
    ("model", "family"),
    [
        ("claude-sonnet-5-5", AIProviderName.ANTHROPIC),
        ("deepseek-v4-pro", AIProviderName.DEEPSEEK),
        ("gpt-5.6-luna", AIProviderName.OPENAI),
        ("o4-mini", AIProviderName.OPENAI),
        ("my-fine-tune", None),
    ],
)
def test_model_family(model, family):
    assert model_family(model) == family


# ------------------------------------------------------------------------------ campaign AI


def test_openai_campaign_payload_is_unchanged(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "o-key")
    monkeypatch.setenv("CAMPAIGN_AI_MODEL", "gpt-5.6-luna")
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "output": [{"content": [{"type": "output_text", "text": DRAFT_JSON}]}],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )

    provider = OpenAICampaignAIProvider(mock_client(handler))
    result = provider.generate(CAMPAIGN_REQUEST)

    body = json.loads(seen[0].content)
    assert str(seen[0].url) == "https://api.openai.com/v1/responses"
    assert seen[0].headers["authorization"] == "Bearer o-key"
    assert set(body) == {"model", "input", "max_output_tokens"}
    assert body["model"] == "gpt-5.6-luna" and body["max_output_tokens"] == 900
    assert (provider.key, result.used_fact_keys) == ("OPENAI", ("industry",))
    assert (result.input_tokens, result.output_tokens) == (10, 5)


def test_anthropic_campaign_provider():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "```json\n" + DRAFT_JSON + "\n```"}],
                "usage": {"input_tokens": 12, "output_tokens": 7},
            },
        )

    provider = build_campaign_ai_provider(
        {"AI_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "a-key"}, mock_client(handler)
    )
    result = provider.generate(CAMPAIGN_REQUEST)

    request = seen[0]
    assert str(request.url) == "https://api.anthropic.com/v1/messages"
    assert request.headers["x-api-key"] == "a-key"
    assert request.headers["anthropic-version"] == "2023-06-01"
    body = json.loads(request.content)
    assert body["model"] == "claude-sonnet-5-5"
    assert body["messages"][0]["role"] == "user"
    assert provider.key == "ANTHROPIC"
    assert result.subject == "Gate queues at Port Alpha"
    assert (result.input_tokens, result.output_tokens) == (12, 7)


def test_deepseek_campaign_provider_uses_json_mode():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": DRAFT_JSON}}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 9},
            },
        )

    provider = build_campaign_ai_provider(
        {"CAMPAIGN_AI_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": "d-key"}, mock_client(handler)
    )
    result = provider.generate(CAMPAIGN_REQUEST)

    request = seen[0]
    assert str(request.url) == "https://api.deepseek.com/chat/completions"
    assert request.headers["authorization"] == "Bearer d-key"
    body = json.loads(request.content)
    assert body["model"] == "deepseek-v4-flash"
    assert body["response_format"] == {"type": "json_object"}
    assert "JSON" in body["messages"][-1]["content"]
    assert (provider.key, result.input_tokens, result.output_tokens) == ("DEEPSEEK", 20, 9)


@pytest.mark.parametrize(
    ("status", "category"),
    [(402, "INSUFFICIENT_BALANCE"), (429, "RATE_LIMITED"), (503, "PROVIDER_UNAVAILABLE")],
)
def test_campaign_errors_are_safe_categories(status, category):
    def handler(_request):
        return httpx.Response(status, json={"error": {"message": "secret detail"}})

    provider = build_campaign_ai_provider(
        {"AI_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": "d"}, mock_client(handler)
    )
    with pytest.raises(CampaignAIProviderError) as error:
        provider.generate(CAMPAIGN_REQUEST)
    assert error.value.category == category


def test_campaign_provider_not_configured_and_invalid_output():
    provider = build_campaign_ai_provider({"AI_PROVIDER": "anthropic"})
    assert provider.configured is False
    with pytest.raises(CampaignAIProviderError) as error:
        provider.generate(CAMPAIGN_REQUEST)
    assert error.value.category == "NOT_CONFIGURED"

    def handler(_request):
        return httpx.Response(200, json={"content": [{"type": "text", "text": "not json"}]})

    provider = build_campaign_ai_provider(
        {"AI_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "a"}, mock_client(handler)
    )
    with pytest.raises(CampaignAIProviderError) as error:
        provider.generate(CAMPAIGN_REQUEST)
    assert error.value.category == "INVALID_PROVIDER_RESPONSE"


# ------------------------------------------------------------------------------ decision makers

ROLE_REPLY = {
    "results": [
        {
            "candidate_id": "a",
            "persona": "Operations leadership",
            "normalized_role": "terminal operations manager",
            "role_match": "HIGH",
            "confidence": 85,
            "reason": "Title names terminal operations",
        }
    ]
}


def test_decision_maker_settings_follow_campaign_provider_and_role_model():
    inherited = resolve_ai_settings(
        AIFeature.DECISION_MAKER,
        {"CAMPAIGN_AI_PROVIDER": "anthropic", "CAMPAIGN_AI_MODEL": "claude-sonnet-5-5"},
    )
    assert (inherited.provider, inherited.model) == (AIProviderName.ANTHROPIC, "claude-sonnet-5-5")

    override = resolve_ai_settings(
        AIFeature.DECISION_MAKER,
        {
            "CAMPAIGN_AI_PROVIDER": "openai",
            "CAMPAIGN_AI_MODEL": "gpt-5.6-luna",
            "DECISION_MAKER_AI_PROVIDER": "deepseek",
        },
    )
    # The campaign's OpenAI model is ignored rather than sent to DeepSeek.
    assert (override.provider, override.model) == (AIProviderName.DEEPSEEK, "deepseek-v4-flash")
    assert override.error is None

    explicit = resolve_ai_settings(
        AIFeature.DECISION_MAKER, {"AI_PROVIDER": "deepseek", "ROLE_RESOLVER_MODEL": "gpt-5"}
    )
    assert "ROLE_RESOLVER_MODEL" in explicit.error

    default = resolve_ai_settings(AIFeature.DECISION_MAKER, {"OPENAI_API_KEY": "k"})
    assert (default.provider, default.model, default.timeout_seconds) == (
        AIProviderName.OPENAI,
        "gpt-5.6-luna",
        30,
    )


def test_decision_maker_on_anthropic_instructs_the_schema():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "Here you go: " + json.dumps(ROLE_REPLY)}],
                "usage": {"input_tokens": 40, "output_tokens": 20},
            },
        )

    ai = build_decision_maker_ai_client(
        {"AI_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "a"}, mock_client(handler)
    )
    results = ai.resolve_roles([{"candidate_id": "a", "title": "Ops Manager"}], [{"name": "Ops"}])

    body = seen[0]
    assert body["model"] == "claude-haiku-5-5"
    assert "JSON Schema" in body["system"] and '"role_match"' in body["system"]
    assert results[0]["role_match"] == "HIGH"
    assert ai.last_usage == {"input_tokens": 40, "output_tokens": 20}
    assert ai.status()["provider"] == "anthropic"


def test_decision_maker_on_deepseek_uses_json_mode():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps({"people": []})}}],
                "usage": {"prompt_tokens": 30, "completion_tokens": 5},
            },
        )

    ai = build_decision_maker_ai_client(
        {"AI_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": "d"}, mock_client(handler)
    )
    assert ai.extract_people("Port Alpha", "https://portalpha.example/team", "Team page") == []
    body = seen[0]
    assert body["response_format"] == {"type": "json_object"}
    assert '"people"' in body["messages"][0]["content"]


def test_decision_maker_errors_keep_existing_codes():
    ai = build_decision_maker_ai_client(
        {"AI_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "a"},
        mock_client(lambda _r: httpx.Response(429)),
    )
    with pytest.raises(DecisionMakerAIError, match="RATE_LIMITED"):
        ai.resolve_roles([], [])
    with pytest.raises(DecisionMakerAIError, match="NOT_CONFIGURED"):
        build_decision_maker_ai_client({"AI_PROVIDER": "deepseek"}).resolve_roles([], [])
