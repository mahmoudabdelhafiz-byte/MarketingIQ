from __future__ import annotations

import hashlib
import hmac
import os
import time


class EngagementWebhookNotConfigured(RuntimeError):
    pass


class EngagementWebhookAuthenticationError(RuntimeError):
    pass


def verify_engagement_webhook(
    raw_body: bytes,
    timestamp: str | None,
    signature: str | None,
    *,
    now_epoch: int | None = None,
) -> None:
    secret = os.environ.get("ENGAGEMENT_WEBHOOK_SECRET", "").strip()
    if len(secret) < 32:
        raise EngagementWebhookNotConfigured("Engagement webhook secret is not configured")
    if not timestamp or not signature:
        raise EngagementWebhookAuthenticationError("Webhook signature headers are required")
    try:
        event_epoch = int(timestamp)
    except ValueError:
        raise EngagementWebhookAuthenticationError("Webhook timestamp is invalid") from None

    max_age = _max_age_seconds()
    current = int(time.time()) if now_epoch is None else now_epoch
    if abs(current - event_epoch) > max_age:
        raise EngagementWebhookAuthenticationError(
            "Webhook timestamp is outside the allowed window"
        )

    supplied = signature.strip()
    if supplied.startswith("sha256="):
        supplied = supplied[7:]
    expected = hmac.new(
        secret.encode("utf-8"),
        timestamp.encode("ascii") + b"." + raw_body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(supplied.lower(), expected):
        raise EngagementWebhookAuthenticationError("Webhook signature is invalid")


def _max_age_seconds() -> int:
    raw = os.environ.get("ENGAGEMENT_WEBHOOK_MAX_AGE_SECONDS", "300").strip()
    try:
        value = int(raw)
    except ValueError:
        return 300
    return min(max(value, 30), 3600)
