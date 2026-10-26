"""OpenAI adapter for decision-maker discovery (role resolution and page extraction).

Uses the Responses API with a strict JSON schema. The model only receives evidence already
collected (titles, departments, page text); callers validate every value it returns and fall
back to rules on any failure. Model reasoning is never requested or stored; each item carries
only a one-sentence, evidence-citing explanation.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

from marketingiq.infrastructure.campaign_ai_provider import (
    DEFAULT_MODEL,
    RESPONSES_URL,
    _int_or_none,
    _response_text,
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


class OpenAIDecisionMakerClient:
    key = "OPENAI"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self.api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        self.model = (
            os.environ.get("ROLE_RESOLVER_MODEL", "").strip()
            or os.environ.get("CAMPAIGN_AI_MODEL", "").strip()
            or DEFAULT_MODEL
        )
        self.timeout_seconds = float(os.environ.get("CAMPAIGN_AI_TIMEOUT_SECONDS", "30"))
        self.client = client
        self.last_usage: dict[str, int | None] = {}

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

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
        if not self.configured:
            raise DecisionMakerAIError("NOT_CONFIGURED")
        body = {
            "model": self.model,
            "instructions": instructions,
            "input": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            "max_output_tokens": max_output_tokens,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "schema": schema,
                    "strict": True,
                }
            },
        }
        http = self.client or httpx.Client(timeout=self.timeout_seconds)
        try:
            response = http.post(
                RESPONSES_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=self.timeout_seconds,
            )
        except (httpx.TimeoutException, httpx.NetworkError):
            raise DecisionMakerAIError("NETWORK_ERROR") from None
        except httpx.HTTPError:
            raise DecisionMakerAIError("NETWORK_ERROR") from None
        if response.status_code == 429:
            raise DecisionMakerAIError("RATE_LIMITED")
        if response.status_code in {401, 403}:
            raise DecisionMakerAIError("AUTHENTICATION_ERROR")
        if response.status_code >= 500:
            raise DecisionMakerAIError("PROVIDER_UNAVAILABLE")
        if response.status_code >= 400:
            raise DecisionMakerAIError("PROVIDER_REJECTED_REQUEST")
        try:
            data = response.json()
            parsed = json.loads(_response_text(data))
        except (ValueError, TypeError, AttributeError, json.JSONDecodeError):
            raise DecisionMakerAIError("INVALID_PROVIDER_RESPONSE") from None
        if not isinstance(parsed, dict):
            raise DecisionMakerAIError("INVALID_PROVIDER_RESPONSE")
        usage = data.get("usage") or {}
        self.last_usage = {
            "input_tokens": _int_or_none(usage.get("input_tokens")),
            "output_tokens": _int_or_none(usage.get("output_tokens")),
        }
        return parsed

    def status(self) -> dict[str, Any]:
        return {
            "provider_key": self.key,
            "configured": self.configured,
            "model": self.model,
            "prompt_version": PROMPT_VERSION,
        }
