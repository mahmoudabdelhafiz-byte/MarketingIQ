from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from typing import Any

import httpx

from marketingiq.domain.campaign_ai import (
    CampaignAIProviderError,
    CampaignAIRequest,
    CampaignAIResult,
)
from marketingiq.infrastructure.ai_config import (
    DEFAULT_MODELS,
    AIFeature,
    AIProviderName,
    AIProviderSettings,
    resolve_ai_settings,
)
from marketingiq.infrastructure.llm_clients import (
    OPENAI_RESPONSES_URL,
    LLMError,
    build_llm_client,
    extract_json_object,
)

PROMPT_VERSION = "grounded-campaign-ai-v1"
DEFAULT_MODEL = DEFAULT_MODELS[(AIFeature.CAMPAIGN, AIProviderName.OPENAI)]
RESPONSES_URL = OPENAI_RESPONSES_URL
MAX_OUTPUT_TOKENS = 900


class LLMCampaignAIProvider:
    """Grounded campaign drafting on whichever provider the settings select."""

    def __init__(self, settings: AIProviderSettings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self.key = settings.provider_label.upper()
        self.model = settings.model
        self._client = build_llm_client(settings, client) if settings.provider else None

    @property
    def configured(self) -> bool:
        return self.settings.configured

    def generate(self, request: CampaignAIRequest) -> CampaignAIResult:
        if self._client is None or not self.configured:
            raise CampaignAIProviderError("NOT_CONFIGURED")
        try:
            response = self._client.complete(_prompt(request), max_output_tokens=MAX_OUTPUT_TOKENS)
        except LLMError as error:
            raise CampaignAIProviderError(error.category) from None
        try:
            parsed = extract_json_object(response.text)
            subject = str(parsed["subject"]).strip()
            body = str(parsed["body"]).strip()
            used_fact_keys = tuple(sorted({str(x) for x in parsed.get("used_fact_keys", [])}))
        except (ValueError, KeyError, TypeError, AttributeError, json.JSONDecodeError):
            raise CampaignAIProviderError("INVALID_PROVIDER_RESPONSE") from None
        return CampaignAIResult(
            subject=subject,
            body=body,
            used_fact_keys=used_fact_keys,
            model=self.model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )

    def status(self) -> dict[str, Any]:
        return {
            "provider_key": self.key,
            "provider": self.settings.provider_label,
            "configured": self.configured,
            "model": self.model,
            "prompt_version": PROMPT_VERSION,
            "configuration_error": self.settings.error,
        }


class OpenAICampaignAIProvider(LLMCampaignAIProvider):
    """Backwards-compatible OpenAI-only constructor (reads OPENAI_API_KEY / CAMPAIGN_AI_MODEL)."""

    def __init__(self, client: httpx.Client | None = None) -> None:
        env = dict(os.environ)
        env["CAMPAIGN_AI_PROVIDER"] = AIProviderName.OPENAI.value
        super().__init__(resolve_ai_settings(AIFeature.CAMPAIGN, env), client)


def build_campaign_ai_provider(
    env: Mapping[str, str] | None = None, client: httpx.Client | None = None
) -> LLMCampaignAIProvider:
    return LLMCampaignAIProvider(resolve_ai_settings(AIFeature.CAMPAIGN, env), client)


def request_fingerprint(request: CampaignAIRequest) -> str:
    safe = {
        "company_name": request.company_name,
        "buyer_role": request.buyer_role,
        "product_name": request.product_name,
        "value_proposition": request.value_proposition,
        "call_to_action": request.call_to_action,
        "facts": [(fact.fact_key, fact.value) for fact in request.grounding_facts],
    }
    return hashlib.sha256(
        json.dumps(safe, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _prompt(request: CampaignAIRequest) -> str:
    facts = [
        {"key": fact.fact_key, "value": fact.value}
        for fact in request.grounding_facts
    ]
    first_name = request.recipient_first_name or "there"
    instructions = {
        "task": "Draft a concise B2B introduction email using only the supplied facts.",
        "rules": [
            (
                "Do not invent pain points, budgets, projects, timing, intent, results, "
                "metrics, or relationships."
            ),
            "Do not claim the recipient has a problem unless an approved fact explicitly says so.",
            "Do not introduce any company fact not present in approved_facts.",
            (
                "The product value proposition may be rephrased but not expanded with "
                "new capabilities."
            ),
            "Keep the tone professional, specific, and non-manipulative.",
            "Return JSON only with keys subject, body, used_fact_keys.",
            "used_fact_keys must contain only keys actually referenced in the copy.",
        ],
        "recipient_first_name": first_name,
        "buyer_role": request.buyer_role,
        "company_name": request.company_name,
        "product_name": request.product_name,
        "product_value_proposition": request.value_proposition,
        "call_to_action": request.call_to_action,
        "approved_facts": facts,
    }
    return json.dumps(instructions, ensure_ascii=False, separators=(",", ":"))
