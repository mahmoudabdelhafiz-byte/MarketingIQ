from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


@dataclass(frozen=True)
class DeploymentConfigReport:
    errors: tuple[str, ...]
    integrations: Mapping[str, bool]

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, object]:
        return {
            "status": "ok" if self.ok else "invalid",
            "errors": list(self.errors),
            "integrations": dict(self.integrations),
        }


def validate_deployment_environment(
    env: Mapping[str, str] | None = None,
) -> DeploymentConfigReport:
    """Validate deployment configuration without opening network connections."""

    values = os.environ if env is None else env
    errors: list[str] = []

    database_url = values.get("DATABASE_URL", "").strip()
    if not database_url:
        errors.append("DATABASE_URL is required")
    else:
        try:
            parsed_url = make_url(database_url)
        except ArgumentError:
            errors.append("DATABASE_URL is invalid")
        else:
            if parsed_url.drivername != "mysql+pymysql":
                errors.append("DATABASE_URL must use mysql+pymysql for shared-hosting deployment")
            if parsed_url.password == "change-me":
                errors.append("DATABASE_URL must not use the example database password")

    auth_secret = values.get("AUTH_SECRET", "")
    if len(auth_secret) < 32:
        errors.append("AUTH_SECRET must contain at least 32 characters")
    elif auth_secret == "replace-with-a-random-development-value":
        errors.append("AUTH_SECRET must not use the example development value")

    _validate_int(
        values,
        errors,
        "DB_POOL_RECYCLE_SECONDS",
        default="280",
        minimum=1,
    )
    _validate_int(
        values,
        errors,
        "AUTOMATION_CRON_BATCH_SIZE",
        default="20",
        minimum=1,
        maximum=100,
    )
    _validate_int(
        values,
        errors,
        "PIPELINE_SYNC_BATCH_SIZE",
        default="100",
        minimum=1,
        maximum=500,
    )
    _validate_int(
        values,
        errors,
        "MAILBOX_ENGAGEMENT_BATCH_SIZE",
        default="100",
        minimum=1,
        maximum=1000,
    )

    smtp_enabled = _any_value(
        values,
        "SMTP_HOST",
        "SMTP_USERNAME",
        "SMTP_PASSWORD",
        "SMTP_FROM_EMAIL",
    )
    if smtp_enabled:
        _require(values, errors, "SMTP_HOST")
        _require(values, errors, "SMTP_FROM_EMAIL")
        _validate_paired(values, errors, "SMTP_USERNAME", "SMTP_PASSWORD")
        _validate_int(
            values,
            errors,
            "SMTP_PORT",
            default="587",
            minimum=1,
            maximum=65535,
        )
        smtp_ssl = _validate_bool(values, errors, "SMTP_USE_SSL", default=False)
        smtp_starttls = _validate_bool(values, errors, "SMTP_STARTTLS", default=True)
        if smtp_ssl is True and smtp_starttls is True:
            errors.append("SMTP_USE_SSL and SMTP_STARTTLS cannot both be true")

    imap_enabled = _any_value(values, "IMAP_HOST", "IMAP_USERNAME", "IMAP_PASSWORD")
    if imap_enabled:
        _require(values, errors, "IMAP_HOST")
        _require(values, errors, "IMAP_USERNAME")
        _require(values, errors, "IMAP_PASSWORD")
        _require(values, errors, "IMAP_MAILBOX", default="INBOX")
        _validate_int(
            values,
            errors,
            "IMAP_PORT",
            default="993",
            minimum=1,
            maximum=65535,
        )
        imap_ssl = _validate_bool(values, errors, "IMAP_USE_SSL", default=True)
        imap_starttls = _validate_bool(values, errors, "IMAP_STARTTLS", default=False)
        if imap_ssl is True and imap_starttls is True:
            errors.append("IMAP_USE_SSL and IMAP_STARTTLS cannot both be true")

    openai_enabled = bool(values.get("OPENAI_API_KEY", "").strip())
    if openai_enabled:
        _require(values, errors, "CAMPAIGN_AI_MODEL", default="gpt-5.6-luna")
        _validate_float(
            values,
            errors,
            "CAMPAIGN_AI_TIMEOUT_SECONDS",
            default="30",
            minimum_exclusive=0,
        )

    webhook_enabled = bool(values.get("ENGAGEMENT_WEBHOOK_SECRET", "").strip())
    if webhook_enabled:
        if len(values.get("ENGAGEMENT_WEBHOOK_SECRET", "").strip()) < 32:
            errors.append("ENGAGEMENT_WEBHOOK_SECRET must contain at least 32 characters")
        _validate_int(
            values,
            errors,
            "ENGAGEMENT_WEBHOOK_MAX_AGE_SECONDS",
            default="300",
            minimum=30,
            maximum=3600,
        )

    integrations = {
        "smtp": smtp_enabled,
        "imap": imap_enabled,
        "openai": openai_enabled,
        "hunter": bool(values.get("HUNTER_API_KEY", "").strip()),
        "engagement_webhook": webhook_enabled,
    }
    return DeploymentConfigReport(tuple(errors), integrations)


def _any_value(env: Mapping[str, str], *names: str) -> bool:
    return any(bool(env.get(name, "").strip()) for name in names)


def _require(
    env: Mapping[str, str],
    errors: list[str],
    name: str,
    *,
    default: str | None = None,
) -> None:
    value = env.get(name, default or "").strip()
    if not value:
        errors.append(f"{name} is required when its integration is enabled")


def _validate_paired(
    env: Mapping[str, str],
    errors: list[str],
    first: str,
    second: str,
) -> None:
    first_set = bool(env.get(first, "").strip())
    second_set = bool(env.get(second, "").strip())
    if first_set != second_set:
        errors.append(f"{first} and {second} must be configured together")


def _validate_bool(
    env: Mapping[str, str],
    errors: list[str],
    name: str,
    *,
    default: bool,
) -> bool | None:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    errors.append(f"{name} must be a boolean value")
    return None


def _validate_int(
    env: Mapping[str, str],
    errors: list[str],
    name: str,
    *,
    default: str,
    minimum: int,
    maximum: int | None = None,
) -> int | None:
    raw = env.get(name, default).strip()
    try:
        value = int(raw)
    except ValueError:
        errors.append(f"{name} must be an integer")
        return None
    if value < minimum or (maximum is not None and value > maximum):
        if maximum is None:
            errors.append(f"{name} must be at least {minimum}")
        else:
            errors.append(f"{name} must be between {minimum} and {maximum}")
        return None
    return value


def _validate_float(
    env: Mapping[str, str],
    errors: list[str],
    name: str,
    *,
    default: str,
    minimum_exclusive: float,
) -> float | None:
    raw = env.get(name, default).strip()
    try:
        value = float(raw)
    except ValueError:
        errors.append(f"{name} must be a number")
        return None
    if value <= minimum_exclusive:
        errors.append(f"{name} must be greater than {minimum_exclusive:g}")
        return None
    return value
