"""Minimal text-completion clients for OpenAI, Anthropic (Claude) and DeepSeek.

Each client sends one prompt and returns the reply text plus token usage. HTTP failures become an
``LLMError`` with a safe category; provider error bodies, prompts and keys are never surfaced.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from marketingiq.infrastructure.ai_config import AIProviderName, AIProviderSettings

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"


class LLMError(RuntimeError):
    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


@dataclass(frozen=True)
class LLMResponse:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class LLMClient(Protocol):
    provider: AIProviderName
    model: str

    def complete(
        self, prompt: str, *, max_output_tokens: int, system: str | None = None
    ) -> LLMResponse: ...


class _HttpClient:
    def __init__(self, settings: AIProviderSettings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self.model = settings.model
        self._client = client

    def post_json(self, url: str, headers: dict[str, str], payload: dict[str, Any]) -> dict:
        if not self.settings.api_key:
            raise LLMError("NOT_CONFIGURED")
        try:
            if self._client is not None:
                response = self._client.post(
                    url, headers=headers, json=payload, timeout=self.settings.timeout_seconds
                )
            else:
                response = httpx.post(
                    url, headers=headers, json=payload, timeout=self.settings.timeout_seconds
                )
        except (httpx.TimeoutException, httpx.NetworkError):
            raise LLMError("NETWORK_ERROR") from None
        raise_for_status(response.status_code)
        try:
            data = response.json()
        except ValueError:
            raise LLMError("INVALID_PROVIDER_RESPONSE") from None
        if not isinstance(data, dict):
            raise LLMError("INVALID_PROVIDER_RESPONSE")
        return data


def raise_for_status(status_code: int) -> None:
    if status_code < 400:
        return
    if status_code == 429:
        raise LLMError("RATE_LIMITED")
    if status_code in {401, 403}:
        raise LLMError("AUTHENTICATION_ERROR")
    if status_code == 402:
        raise LLMError("INSUFFICIENT_BALANCE")
    if status_code >= 500:
        raise LLMError("PROVIDER_UNAVAILABLE")
    raise LLMError("PROVIDER_REJECTED_REQUEST")


class OpenAIResponsesClient(_HttpClient):
    provider = AIProviderName.OPENAI

    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
        }

    def complete(
        self, prompt: str, *, max_output_tokens: int, system: str | None = None
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "input": prompt,
            "max_output_tokens": max_output_tokens,
        }
        if system:
            payload["instructions"] = system
        data = self.post_json(OPENAI_RESPONSES_URL, self.headers(), payload)
        usage = data.get("usage") or {}
        return LLMResponse(
            text=openai_output_text(data),
            input_tokens=int_or_none(usage.get("input_tokens")),
            output_tokens=int_or_none(usage.get("output_tokens")),
        )


class AnthropicMessagesClient(_HttpClient):
    provider = AIProviderName.ANTHROPIC

    def headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.settings.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

    def complete(
        self, prompt: str, *, max_output_tokens: int, system: str | None = None
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_output_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            payload["system"] = system
        data = self.post_json(ANTHROPIC_MESSAGES_URL, self.headers(), payload)
        blocks = data.get("content")
        if not isinstance(blocks, list):
            raise LLMError("INVALID_PROVIDER_RESPONSE")
        text = "".join(
            block["text"]
            for block in blocks
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        ).strip()
        if not text:
            raise LLMError("INVALID_PROVIDER_RESPONSE")
        usage = data.get("usage") or {}
        return LLMResponse(
            text=text,
            input_tokens=int_or_none(usage.get("input_tokens")),
            output_tokens=int_or_none(usage.get("output_tokens")),
        )


class DeepSeekChatClient(_HttpClient):
    """OpenAI-compatible chat completions with DeepSeek's JSON output mode."""

    provider = AIProviderName.DEEPSEEK

    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
        }

    def complete(
        self, prompt: str, *, max_output_tokens: int, system: str | None = None
    ) -> LLMResponse:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_output_tokens,
            # Prompts sent through this client always ask for JSON, as JSON mode requires.
            "response_format": {"type": "json_object"},
        }
        data = self.post_json(f"{self.settings.base_url}/chat/completions", self.headers(), payload)
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise LLMError("INVALID_PROVIDER_RESPONSE") from None
        if not isinstance(text, str) or not text.strip():
            raise LLMError("INVALID_PROVIDER_RESPONSE")
        usage = data.get("usage") or {}
        return LLMResponse(
            text=text.strip(),
            input_tokens=int_or_none(usage.get("prompt_tokens")),
            output_tokens=int_or_none(usage.get("completion_tokens")),
        )


def build_llm_client(
    settings: AIProviderSettings, client: httpx.Client | None = None
) -> OpenAIResponsesClient | AnthropicMessagesClient | DeepSeekChatClient:
    if settings.provider == AIProviderName.ANTHROPIC:
        return AnthropicMessagesClient(settings, client)
    if settings.provider == AIProviderName.DEEPSEEK:
        return DeepSeekChatClient(settings, client)
    return OpenAIResponsesClient(settings, client)


def openai_output_text(data: dict[str, Any]) -> str:
    for output in data.get("output") or []:
        if not isinstance(output, dict):
            continue
        for content in output.get("content") or []:
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
    raise LLMError("INVALID_PROVIDER_RESPONSE")


def strip_json_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`").strip()
        if stripped.lower().startswith("json"):
            stripped = stripped[4:]
    return stripped.strip()


def int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
