"""AI reply suggestions on the configured campaign AI provider (OpenAI, Claude or DeepSeek)."""

from __future__ import annotations

import json
from collections.abc import Mapping

import httpx

from marketingiq.application.replies import (
    ReplyAIError,
    ReplyAIRequest,
    ReplyAIResult,
)
from marketingiq.domain.replies import ReplyIntent
from marketingiq.infrastructure.ai_config import AIFeature, AIProviderSettings, resolve_ai_settings
from marketingiq.infrastructure.llm_clients import (
    JSONSchema,
    LLMError,
    build_llm_client,
    extract_json_object,
)

PROMPT_VERSION = "reply-assistant-v1"
MAX_OUTPUT_TOKENS = 900

SCHEMA = JSONSchema(
    name="reply_suggestion",
    schema={
        "type": "object",
        "additionalProperties": False,
        "required": ["intent", "confidence", "summary", "subject", "body"],
        "properties": {
            "intent": {"type": "string", "enum": [item.value for item in ReplyIntent]},
            "confidence": {"type": "number"},
            "summary": {"type": "string"},
            "subject": {"type": "string"},
            "body": {"type": "string"},
        },
    },
)

SYSTEM = (
    "You help a B2B seller answer a prospect's email reply. You write the answer the seller "
    "will review and send. You never invent facts."
)


def build_prompt(request: ReplyAIRequest) -> str:
    return json.dumps(
        {
            "task": "Classify the prospect's reply and draft the seller's answer.",
            "rules": [
                "Answer only from product_value_proposition and knowledge_base. If the answer "
                "is not there, say you will confirm the details and come back shortly.",
                "Never invent prices, discounts, features, integrations, clients, results, "
                "timelines or links.",
                "If they want a meeting or are interested, offer exactly the proposed_times and, "
                "if booking_url is given, that link. Do not propose other times.",
                "If they ask to be removed or unsubscribed, confirm briefly that they are "
                "removed; offer nothing else.",
                "If they are not interested, thank them and close politely without pushing.",
                "If it is an automatic out-of-office message, set body to an empty string.",
                f"Write subject and body in {request.language_name}, naturally, under 120 words.",
                "Start with a greeting using the prospect's first name if given. End with a "
                "closing line only; no name or signature.",
                "subject: keep the conversation subject, starting with 'Re: '.",
                "summary: one short English sentence describing what the prospect wants.",
                "confidence: 0 to 1, how sure you are of the intent.",
            ],
            "intent_values": {
                "MEETING_REQUEST": "asks for or agrees to a call/meeting/demo",
                "INTERESTED": "positive, wants more information",
                "QUESTION": "asks a specific question",
                "NOT_NOW": "maybe later",
                "NOT_INTERESTED": "declines",
                "UNSUBSCRIBE": "asks not to be contacted",
                "OUT_OF_OFFICE": "automatic absence reply",
                "OTHER": "anything else",
            },
            "prospect_first_name": request.prospect_first_name,
            "company_name": request.company_name,
            "product_name": request.product_name,
            "product_value_proposition": request.value_proposition,
            "knowledge_base": request.knowledge_base,
            "booking_url": request.booking_url,
            "proposed_times": list(request.proposed_times),
            "our_original_email": {
                "subject": request.original_subject,
                "body": request.original_body,
            },
            "prospect_reply": request.reply_text,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


class LLMReplyAIProvider:
    def __init__(self, settings: AIProviderSettings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self.model = settings.model
        self._client = build_llm_client(settings, client) if settings.provider else None

    @property
    def configured(self) -> bool:
        return self.settings.configured

    def suggest(self, request: ReplyAIRequest) -> ReplyAIResult:
        if self._client is None or not self.configured:
            raise ReplyAIError("NOT_CONFIGURED")
        try:
            response = self._client.complete(
                build_prompt(request),
                max_output_tokens=MAX_OUTPUT_TOKENS,
                system=SYSTEM,
                json_schema=SCHEMA,
            )
        except LLMError as error:
            raise ReplyAIError(error.category) from None
        try:
            data = extract_json_object(response.text)
            intent = ReplyIntent(str(data["intent"]).strip().upper())
            confidence = data.get("confidence")
            confidence = (
                max(0.0, min(1.0, float(confidence)))
                if isinstance(confidence, int | float)
                else None
            )
            return ReplyAIResult(
                intent=intent,
                confidence=confidence,
                summary=str(data.get("summary") or "").strip(),
                subject=str(data.get("subject") or "").strip(),
                body=str(data.get("body") or "").strip(),
                model=self.model,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
            )
        except (ValueError, KeyError, TypeError, AttributeError, json.JSONDecodeError):
            raise ReplyAIError("INVALID_PROVIDER_RESPONSE") from None


def build_reply_ai_provider(
    env: Mapping[str, str] | None = None, client: httpx.Client | None = None
) -> LLMReplyAIProvider:
    return LLMReplyAIProvider(resolve_ai_settings(AIFeature.CAMPAIGN, env), client)
