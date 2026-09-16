from __future__ import annotations

import json
import os
from typing import Any

import httpx

from marketingiq.domain.campaign_generation import (
    CampaignCTAStyle,
    CampaignGenerationContext,
    CampaignOpeningStyle,
    CampaignStrategyGenerator,
    CampaignStrategyResult,
    CampaignSubjectStyle,
)
from marketingiq.domain.providers import (
    ProviderAuthenticationError,
    ProviderError,
    ProviderMalformedResponse,
    ProviderNotConfigured,
    ProviderRateLimited,
)


class CampaignGeneratorRegistry:
    def __init__(self, generators: list[CampaignStrategyGenerator]) -> None:
        self._generators = {generator.key.upper(): generator for generator in generators}

    def get(self, key: str) -> CampaignStrategyGenerator:
        try:
            return self._generators[key.upper()]
        except KeyError as error:
            raise ValueError(f"Unknown campaign generator: {key}") from error

    def status(self) -> list[dict[str, Any]]:
        return [
            {
                "provider_key": generator.key,
                "configured": generator.configured,
                "model": generator.model or None,
                "costs_credits": generator.costs_credits,
            }
            for generator in sorted(self._generators.values(), key=lambda item: item.key)
        ]


class OpenAIResponsesCampaignStrategyGenerator:
    """Select a bounded campaign strategy using the OpenAI Responses API.

    The model never returns campaign prose. It selects only from enumerated
    subject/opening/CTA styles plus a known redistributable evidence identifier.
    MarketingIQ renders the final copy locally from trusted inputs.
    """

    key = "OPENAI"
    costs_credits = True

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float = 20.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._api_key = api_key if api_key is not None else os.getenv("OPENAI_API_KEY")
        self._model = model if model is not None else os.getenv("OPENAI_CAMPAIGN_MODEL", "")
        self._base_url = (base_url or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip(
            "/"
        )
        self._timeout_seconds = timeout_seconds
        self._client = client

    @property
    def configured(self) -> bool:
        return bool((self._api_key or "").strip() and self._model.strip())

    @property
    def model(self) -> str:
        return self._model.strip()

    def generate(self, context: CampaignGenerationContext) -> CampaignStrategyResult:
        if not self.configured:
            raise ProviderNotConfigured("OpenAI campaign generation is not configured")

        allowed_evidence_ids = [item.evidence_id for item in context.evidence_options]
        selected_ids = ["NONE", *allowed_evidence_ids]
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "subject_style": {
                    "type": "string",
                    "enum": [item.value for item in CampaignSubjectStyle],
                },
                "opening_style": {
                    "type": "string",
                    "enum": [item.value for item in CampaignOpeningStyle],
                },
                "cta_style": {
                    "type": "string",
                    "enum": [item.value for item in CampaignCTAStyle],
                },
                "selected_evidence_id": {
                    "type": "string",
                    "enum": selected_ids,
                },
            },
            "required": [
                "subject_style",
                "opening_style",
                "cta_style",
                "selected_evidence_id",
            ],
        }
        system_prompt = (
            "You are a B2B outreach strategy selector. Do not write campaign copy. "
            "Choose only from the supplied enum values and evidence identifiers. "
            "Do not infer customer pain, intent, budget, timing, ROI, savings, urgency, "
            "or any fact not explicitly supplied. Prefer NONE when evidence would feel "
            "unnatural or intrusive. The final message will be rendered by MarketingIQ."
        )
        context_payload = {
            "company_name": context.company_name,
            "product_name": context.product_name,
            "value_proposition": context.value_proposition,
            "contact_first_name": context.contact_first_name,
            "contact_job_title": context.contact_job_title,
            "recommended_buyer_role": context.recommended_buyer_role,
            "qualification_status": context.qualification_status,
            "fit_score": context.fit_score,
            "fit_grade": context.fit_grade,
            "evidence_options": [
                {
                    "evidence_id": item.evidence_id,
                    "fact_key": item.fact_key,
                    "value": item.value,
                }
                for item in context.evidence_options
            ],
        }
        request = {
            "model": self.model,
            "store": False,
            "input": [
                {
                    "role": "system",
                    "content": [{"type": "input_text", "text": system_prompt}],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": json.dumps(context_payload, separators=(",", ":")),
                        }
                    ],
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "campaign_strategy",
                    "strict": True,
                    "schema": schema,
                }
            },
        }

        try:
            if self._client is not None:
                response = self._client.post(
                    f"{self._base_url}/responses",
                    headers=self._headers(),
                    json=request,
                    timeout=self._timeout_seconds,
                )
            else:
                with httpx.Client(timeout=self._timeout_seconds) as client:
                    response = client.post(
                        f"{self._base_url}/responses",
                        headers=self._headers(),
                        json=request,
                    )
        except httpx.TimeoutException as error:
            raise ProviderError("Campaign generation provider timed out") from error
        except httpx.HTTPError as error:
            raise ProviderError("Campaign generation provider request failed") from error

        if response.status_code in {401, 403}:
            raise ProviderAuthenticationError("Campaign generation provider authentication failed")
        if response.status_code == 429:
            raise ProviderRateLimited("Campaign generation provider rate limited the request")
        if response.status_code >= 400:
            raise ProviderError("Campaign generation provider returned an error")

        try:
            payload = response.json()
            raw_text = self._output_text(payload)
            selection = json.loads(raw_text)
            result = CampaignStrategyResult(
                provider_key=self.key,
                model=self.model,
                subject_style=CampaignSubjectStyle(selection["subject_style"]),
                opening_style=CampaignOpeningStyle(selection["opening_style"]),
                cta_style=CampaignCTAStyle(selection["cta_style"]),
                selected_evidence_id=(
                    None
                    if selection["selected_evidence_id"] == "NONE"
                    else selection["selected_evidence_id"]
                ),
                request_identifier=payload.get("id"),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ProviderMalformedResponse("Campaign generation provider response was invalid") from error

        if result.selected_evidence_id is not None and result.selected_evidence_id not in allowed_evidence_ids:
            raise ProviderMalformedResponse("Campaign generation selected unknown evidence")
        return result

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _output_text(payload: dict[str, Any]) -> str:
        direct = payload.get("output_text")
        if isinstance(direct, str) and direct.strip():
            return direct
        for output_item in payload.get("output", []):
            if not isinstance(output_item, dict):
                continue
            for content in output_item.get("content", []):
                if (
                    isinstance(content, dict)
                    and content.get("type") == "output_text"
                    and isinstance(content.get("text"), str)
                ):
                    return content["text"]
        raise ProviderMalformedResponse("Campaign generation provider returned no text output")
