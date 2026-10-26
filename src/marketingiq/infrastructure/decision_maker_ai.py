"""AI adapter for decision-maker discovery (role resolution and page extraction).

Runs on OpenAI, Claude or DeepSeek (see ``ai_config``). OpenAI enforces the JSON schema natively;
the others are instructed to follow it. The model only receives evidence already
collected (titles, departments, page text); callers validate every value it returns and fall
back to rules on any failure. Model reasoning is never requested or stored; each item carries
only a one-sentence, evidence-citing explanation.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any

import httpx

from marketingiq.infrastructure.ai_config import (
    AIFeature,
    AIProviderName,
    AIProviderSettings,
    resolve_ai_settings,
)
from marketingiq.infrastructure.llm_clients import (
    JSONSchema,
    LLMError,
    build_llm_client,
    extract_json_object,
)

PROMPT_VERSION = "decision-maker-roles-v1"
MAX_PAGE_CHARS = 12_000


class DecisionMakerAIError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


ROLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["results"],
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "candidate_id",
                    "persona",
                    "normalized_role",
                    "role_match",
                    "confidence",
                    "reason",
                ],
                "properties": {
                    "candidate_id": {"type": "string"},
                    "persona": {"type": ["string", "null"]},
                    "normalized_role": {"type": "string"},
                    "role_match": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW", "NONE"]},
                    "confidence": {"type": "integer"},
                    "reason": {"type": "string"},
                },
            },
        }
    },
}

PEOPLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["people"],
    "properties": {
        "people": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "title"],
                "properties": {
                    "name": {"type": "string"},
                    "title": {"type": "string"},
                },
            },
        }
    },
}


class LLMDecisionMakerClient:
    def __init__(self, settings: AIProviderSettings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self.key = settings.provider_label.upper()
        self.model = settings.model
        self.timeout_seconds = settings.timeout_seconds
        self._llm = build_llm_client(settings, client) if settings.provider else None
        self.last_usage: dict[str, int | None] = {}

    @property
    def configured(self) -> bool:
        return self.settings.configured

    def resolve_roles(
        self, candidates: list[dict[str, Any]], personas: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        instructions = (
            "You map job titles to target buyer personas for B2B outreach. Use only the "
            "title, department and seniority supplied for each candidate; do not assume "
            "facts about the person or company. For each candidate return: persona (one of "
            "the supplied persona names, or null), normalized_role (the role in plain words), "
            "role_match (HIGH = holds the persona's role; MEDIUM = senior in that function "
            "but a different or narrower role; LOW = junior or supporting role; NONE = "
            "unrelated), confidence 0-100, and reason: one short sentence citing the title "
            "words that justify the match. Return every candidate_id exactly once."
        )
        data = self._call(
            instructions,
            {"personas": personas, "candidates": candidates},
            "role_resolution",
            ROLE_SCHEMA,
            max_output_tokens=2000,
        )
        results = data.get("results")
        if not isinstance(results, list):
            raise DecisionMakerAIError("INVALID_PROVIDER_RESPONSE")
        return results

    def extract_people(
        self, company_name: str, page_url: str, page_text: str
    ) -> list[dict[str, Any]]:
        instructions = (
            "Extract people named on this company web page together with the job title the "
            "page gives them. Copy each name and title exactly as written on the page. Only "
            f"include people the page presents as working at {company_name}. Omit anyone "
            "without a stated title, anyone described as former, and anyone who is a client, "
            "partner or testimonial author. Return an empty list if unsure."
        )
        data = self._call(
            instructions,
            {"page_url": page_url, "page_text": page_text[:MAX_PAGE_CHARS]},
            "page_people",
            PEOPLE_SCHEMA,
            max_output_tokens=1500,
        )
        people = data.get("people")
        if not isinstance(people, list):
            raise DecisionMakerAIError("INVALID_PROVIDER_RESPONSE")
        return people

    def _call(
        self,
        instructions: str,
        payload: dict[str, Any],
        schema_name: str,
        schema: dict[str, Any],
        *,
        max_output_tokens: int,
    ) -> dict[str, Any]:
        if self._llm is None or not self.configured:
            raise DecisionMakerAIError("NOT_CONFIGURED")
        try:
            response = self._llm.complete(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                max_output_tokens=max_output_tokens,
                system=instructions,
                json_schema=JSONSchema(schema_name, schema),
            )
        except LLMError as error:
            raise DecisionMakerAIError(error.category) from None
        try:
            parsed = extract_json_object(response.text)
        except (ValueError, TypeError):
            raise DecisionMakerAIError("INVALID_PROVIDER_RESPONSE") from None
        if not isinstance(parsed, dict):
            raise DecisionMakerAIError("INVALID_PROVIDER_RESPONSE")
        self.last_usage = {
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
        }
        return parsed

    def status(self) -> dict[str, Any]:
        return {
            "provider_key": self.key,
            "provider": self.settings.provider_label,
            "configured": self.configured,
            "model": self.model,
            "prompt_version": PROMPT_VERSION,
            "configuration_error": self.settings.error,
        }


class OpenAIDecisionMakerClient(LLMDecisionMakerClient):
    """Backwards-compatible OpenAI-only constructor (OPENAI_API_KEY / ROLE_RESOLVER_MODEL)."""

    def __init__(self, client: httpx.Client | None = None) -> None:
        env = dict(os.environ)
        env["DECISION_MAKER_AI_PROVIDER"] = AIProviderName.OPENAI.value
        super().__init__(resolve_ai_settings(AIFeature.DECISION_MAKER, env), client)


def build_decision_maker_ai_client(
    env: Mapping[str, str] | None = None, client: httpx.Client | None = None
) -> LLMDecisionMakerClient:
    return LLMDecisionMakerClient(resolve_ai_settings(AIFeature.DECISION_MAKER, env), client)
