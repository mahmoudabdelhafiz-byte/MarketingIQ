from __future__ import annotations

import hashlib
import json
import os
from typing import Any

import httpx

from marketingiq.domain.campaign_ai import (
    CampaignAIProviderError,
    CampaignAIRequest,
    CampaignAIResult,
)

PROMPT_VERSION = "grounded-campaign-ai-v1"
DEFAULT_MODEL = "gpt-5.6-luna"
RESPONSES_URL = "https://api.openai.com/v1/responses"


class OpenAICampaignAIProvider:
    key = "OPENAI"

    def __init__(self) -> None:
        self.api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        self.model = os.environ.get("CAMPAIGN_AI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
        self.timeout_seconds = float(os.environ.get("CAMPAIGN_AI_TIMEOUT_SECONDS", "30"))

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def generate(self, request: CampaignAIRequest) -> CampaignAIResult:
        if not self.configured:
            raise CampaignAIProviderError("NOT_CONFIGURED")

        payload = {
            "model": self.model,
            "input": _prompt(request),
            "max_output_tokens": 900,
        }
        try:
            response = httpx.post(
                RESPONSES_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.timeout_seconds,
            )
        except (httpx.TimeoutException, httpx.NetworkError):
            raise CampaignAIProviderError("NETWORK_ERROR") from None

        if response.status_code == 429:
            raise CampaignAIProviderError("RATE_LIMITED")
        if response.status_code in {401, 403}:
            raise CampaignAIProviderError("AUTHENTICATION_ERROR")
        if response.status_code >= 500:
            raise CampaignAIProviderError("PROVIDER_UNAVAILABLE")
        if response.status_code >= 400:
            raise CampaignAIProviderError("PROVIDER_REJECTED_REQUEST")

        try:
            data = response.json()
            raw_text = _response_text(data)
            parsed = json.loads(raw_text)
            subject = str(parsed["subject"]).strip()
            body = str(parsed["body"]).strip()
            used_fact_keys = tuple(sorted({str(x) for x in parsed.get("used_fact_keys", [])}))
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            raise CampaignAIProviderError("INVALID_PROVIDER_RESPONSE") from None

        usage = data.get("usage") or {}
        return CampaignAIResult(
            subject=subject,
            body=body,
            used_fact_keys=used_fact_keys,
            model=self.model,
            input_tokens=_int_or_none(usage.get("input_tokens")),
            output_tokens=_int_or_none(usage.get("output_tokens")),
        )

    def status(self) -> dict[str, Any]:
        return {
            "provider_key": self.key,
            "configured": self.configured,
            "model": self.model,
            "prompt_version": PROMPT_VERSION,
        }


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
            "Do not invent pain points, budgets, projects, timing, intent, results, metrics, or relationships.",
            "Do not claim the recipient has a problem unless an approved fact explicitly says so.",
            "Do not introduce any company fact not present in approved_facts.",
            "The product value proposition may be rephrased but not expanded with new capabilities.",
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


def _response_text(data: dict[str, Any]) -> str:
    for output in data.get("output") or []:
        for content in output.get("content") or []:
            text = content.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
    raise ValueError("response text missing")


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
