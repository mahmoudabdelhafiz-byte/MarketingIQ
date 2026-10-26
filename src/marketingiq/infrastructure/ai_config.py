"""Which AI provider and model each MarketingIQ feature uses.

One switch, ``AI_PROVIDER`` (``openai`` | ``anthropic`` | ``deepseek``), applies to every AI
feature. Per-feature overrides: ``CAMPAIGN_AI_PROVIDER``, ``DISCOVERY_AI_PROVIDER`` and
``DECISION_MAKER_AI_PROVIDER`` (which falls back to the campaign provider). Models come from
``CAMPAIGN_AI_MODEL``, ``DISCOVERY_AI_MODEL`` and ``ROLE_RESOLVER_MODEL`` when set, otherwise from
the provider's default. The default provider is ``openai`` so existing deployments keep their
behavior.
"""

from __future__ import annotations

import enum
import os
from collections.abc import Mapping
from dataclasses import dataclass


class AIProviderName(enum.StrEnum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    DEEPSEEK = "deepseek"


class AIFeature(enum.StrEnum):
    CAMPAIGN = "CAMPAIGN"
    DISCOVERY = "DISCOVERY"
    DECISION_MAKER = "DECISION_MAKER"


@dataclass(frozen=True)
class _FeatureEnv:
    provider: tuple[str, ...]
    model: str
    # Inherited model settings; ignored (not an error) when they name another vendor's model.
    fallback_models: tuple[str, ...]
    timeout: str


FEATURE_ENV = {
    AIFeature.CAMPAIGN: _FeatureEnv(
        ("CAMPAIGN_AI_PROVIDER", "AI_PROVIDER"), "CAMPAIGN_AI_MODEL", (),
        "CAMPAIGN_AI_TIMEOUT_SECONDS",
    ),
    AIFeature.DISCOVERY: _FeatureEnv(
        ("DISCOVERY_AI_PROVIDER", "AI_PROVIDER"), "DISCOVERY_AI_MODEL", (),
        "DISCOVERY_AI_TIMEOUT_SECONDS",
    ),
    AIFeature.DECISION_MAKER: _FeatureEnv(
        ("DECISION_MAKER_AI_PROVIDER", "CAMPAIGN_AI_PROVIDER", "AI_PROVIDER"),
        "ROLE_RESOLVER_MODEL",
        ("CAMPAIGN_AI_MODEL",),
        "CAMPAIGN_AI_TIMEOUT_SECONDS",
    ),
}


DEFAULT_PROVIDER = AIProviderName.OPENAI

API_KEY_ENV = {
    AIProviderName.OPENAI: "OPENAI_API_KEY",
    AIProviderName.ANTHROPIC: "ANTHROPIC_API_KEY",
    AIProviderName.DEEPSEEK: "DEEPSEEK_API_KEY",
}

DEFAULT_MODELS = {
    (AIFeature.CAMPAIGN, AIProviderName.OPENAI): "gpt-5.6-luna",
    (AIFeature.CAMPAIGN, AIProviderName.ANTHROPIC): "claude-sonnet-5-5",
    (AIFeature.CAMPAIGN, AIProviderName.DEEPSEEK): "deepseek-v4-flash",
    (AIFeature.DISCOVERY, AIProviderName.OPENAI): "gpt-5.5",
    (AIFeature.DISCOVERY, AIProviderName.ANTHROPIC): "claude-sonnet-5-5",
    (AIFeature.DISCOVERY, AIProviderName.DEEPSEEK): "deepseek-v4-flash",
    (AIFeature.DECISION_MAKER, AIProviderName.OPENAI): "gpt-5.6-luna",
    (AIFeature.DECISION_MAKER, AIProviderName.ANTHROPIC): "claude-haiku-5-5",
    (AIFeature.DECISION_MAKER, AIProviderName.DEEPSEEK): "deepseek-v4-flash",
}

DEFAULT_TIMEOUTS = {
    AIFeature.CAMPAIGN: 30.0,
    AIFeature.DISCOVERY: 180.0,
    AIFeature.DECISION_MAKER: 30.0,
}

DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# Recognisable model-name families. A model clearly from another vendor is a configuration error
# (typically CAMPAIGN_AI_MODEL=gpt-... left in place after switching AI_PROVIDER).
_MODEL_FAMILIES = {
    AIProviderName.ANTHROPIC: ("claude",),
    AIProviderName.DEEPSEEK: ("deepseek",),
    AIProviderName.OPENAI: ("gpt", "chatgpt", "o1", "o3", "o4", "codex"),
}


@dataclass(frozen=True)
class AIProviderSettings:
    feature: AIFeature
    provider: AIProviderName | None
    model: str
    api_key: str
    timeout_seconds: float
    base_url: str | None = None
    error: str | None = None

    @property
    def configured(self) -> bool:
        return self.error is None and bool(self.api_key)

    @property
    def provider_label(self) -> str:
        return self.provider.value if self.provider else "invalid"

    def status(self) -> dict[str, object]:
        return {
            "feature": self.feature.value,
            "provider": self.provider_label,
            "model": self.model,
            "configured": self.configured,
            "api_key_env": API_KEY_ENV[self.provider] if self.provider else None,
            "configuration_error": self.error,
        }


def resolve_ai_settings(
    feature: AIFeature, env: Mapping[str, str] | None = None
) -> AIProviderSettings:
    values = os.environ if env is None else env
    spec = FEATURE_ENV[feature]
    chosen = [values.get(name, "").strip() for name in spec.provider]
    raw_provider = next((value for value in chosen if value), DEFAULT_PROVIDER.value).lower()
    timeout = _timeout(values.get(spec.timeout), DEFAULT_TIMEOUTS[feature])
    try:
        provider = AIProviderName(raw_provider)
    except ValueError:
        return AIProviderSettings(
            feature=feature,
            provider=None,
            model="",
            api_key="",
            timeout_seconds=timeout,
            error=f"Unsupported AI provider '{raw_provider}'; use openai, anthropic or deepseek",
        )

    model = DEFAULT_MODELS[(feature, provider)]
    error = None
    explicit_model = values.get(spec.model, "").strip()
    if explicit_model:
        model = explicit_model
        owner = model_family(model)
        if owner is not None and owner != provider:
            error = (
                f"{spec.model} '{model}' looks like a {owner.value} model but the "
                f"{feature.value.lower().replace('_', '-')} AI provider is {provider.value}"
            )
    else:
        for name in spec.fallback_models:
            inherited = values.get(name, "").strip()
            if inherited and model_family(inherited) in (None, provider):
                model = inherited
                break
    base_url = None
    if provider == AIProviderName.DEEPSEEK:
        base_url = (
            values.get("DEEPSEEK_BASE_URL", "").strip() or DEFAULT_DEEPSEEK_BASE_URL
        ).rstrip("/")
    return AIProviderSettings(
        feature=feature,
        provider=provider,
        model=model,
        api_key=values.get(API_KEY_ENV[provider], "").strip(),
        timeout_seconds=timeout,
        base_url=base_url,
        error=error,
    )


def model_family(model: str) -> AIProviderName | None:
    name = model.strip().lower()
    for provider, prefixes in _MODEL_FAMILIES.items():
        if any(name == p or name.startswith(p + "-") or name.startswith(p + ".") for p in prefixes):
            return provider
    return None


def _timeout(raw: str | None, default: float) -> float:
    try:
        value = float(raw) if raw not in (None, "") else default
    except ValueError:
        return default
    return value if value > 0 else default
