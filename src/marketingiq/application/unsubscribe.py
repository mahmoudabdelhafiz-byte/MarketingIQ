"""Signed one-click unsubscribe links and recipient opt-out recording.

Every outbound email carries a link (and RFC 8058 ``List-Unsubscribe`` headers) whose token is an
HMAC-signed ``(organization, email)`` pair. The token needs no database lookup to validate, cannot
be forged or altered to target another tenant or address, and records a tenant-scoped
``OPT_OUT`` suppression that the send gate already enforces.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketingiq.domain.models import AuditLog, Organization
from marketingiq.domain.outbound import SuppressionEntry, SuppressionSource

_KEY_CONTEXT = b"marketingiq-unsubscribe-v1"


class InvalidUnsubscribeToken(ValueError):
    pass


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


@dataclass(frozen=True)
class UnsubscribeRecipient:
    organization_id: str
    email: str


class UnsubscribeLinks:
    def __init__(self, secret: str, public_base_url: str) -> None:
        if len(secret) < 32:
            raise ValueError("Unsubscribe signing requires a secret of at least 32 characters")
        parsed = urlsplit(public_base_url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("MARKETINGIQ_PUBLIC_URL must be an absolute http(s) URL")
        self._key = hmac.new(secret.encode("utf-8"), _KEY_CONTEXT, hashlib.sha256).digest()
        self.base_url = public_base_url.strip().rstrip("/")

    def token(self, organization_id: str, email: str) -> str:
        payload = json.dumps(
            {"o": organization_id, "e": email.strip().lower()},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        signature = hmac.new(self._key, payload, hashlib.sha256).digest()
        return f"{_b64encode(payload)}.{_b64encode(signature)}"

    def url(self, organization_id: str, email: str) -> str:
        return f"{self.base_url}/unsubscribe?{urlencode({'t': self.token(organization_id, email)})}"

    def parse(self, token: str) -> UnsubscribeRecipient:
        try:
            encoded_payload, encoded_signature = token.strip().split(".", 1)
            payload = _b64decode(encoded_payload)
            signature = _b64decode(encoded_signature)
        except (ValueError, TypeError) as error:
            raise InvalidUnsubscribeToken("Malformed unsubscribe link") from error
        expected = hmac.new(self._key, payload, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise InvalidUnsubscribeToken("Unsubscribe link signature is invalid")
        try:
            data = json.loads(payload)
            organization_id = str(data["o"])
            email = str(data["e"])
        except (ValueError, KeyError, TypeError) as error:
            raise InvalidUnsubscribeToken("Malformed unsubscribe link") from error
        if not organization_id or "@" not in email:
            raise InvalidUnsubscribeToken("Malformed unsubscribe link")
        return UnsubscribeRecipient(organization_id, email)


def record_opt_out(session: Session, recipient: UnsubscribeRecipient) -> bool:
    """Suppress the recipient for the tenant. Returns True when a new entry was created."""

    if session.get(Organization, recipient.organization_id) is None:
        raise InvalidUnsubscribeToken("Unsubscribe link is no longer valid")
    existing = session.scalar(
        select(SuppressionEntry).where(
            SuppressionEntry.organization_id == recipient.organization_id,
            SuppressionEntry.email_normalized == recipient.email,
        )
    )
    if existing is not None:
        return False
    entry = SuppressionEntry(
        organization_id=recipient.organization_id,
        email_normalized=recipient.email,
        source=SuppressionSource.OPT_OUT,
        reason="Recipient unsubscribed via email link",
        created_by_user_id=None,
    )
    session.add(entry)
    session.flush()
    session.add(
        AuditLog(
            organization_id=recipient.organization_id,
            actor_user_id=None,
            action="outbound_suppression.recipient_opt_out",
            entity_type="outbound_suppression",
            entity_id=entry.id,
            metadata_json={"source": SuppressionSource.OPT_OUT.value},
        )
    )
    return True
