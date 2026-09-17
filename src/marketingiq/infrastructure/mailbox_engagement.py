from __future__ import annotations

import hashlib
import imaplib
import os
import re
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketingiq.application.engagement import ProviderEngagementIngestionService
from marketingiq.application.errors import ConflictError, NotFoundError
from marketingiq.domain.engagement import EngagementEventType
from marketingiq.domain.outbound import OutboundSendAttempt, OutboundSendStatus

_MESSAGE_ID_RE = re.compile(r"<[^<>\s]+>")


class MailboxProviderError(RuntimeError):
    pass


class MailboxNotConfigured(MailboxProviderError):
    pass


@dataclass(frozen=True)
class MailboxMessage:
    uid: str
    raw: bytes


@dataclass(frozen=True)
class MailboxIngestionResult:
    processed: int
    matched: int
    replies: int
    bounces: int
    skipped: int


@dataclass(frozen=True)
class ParsedMailboxOutcome:
    event_type: EngagementEventType
    reference_message_ids: tuple[str, ...]
    incoming_message_id: str | None
    occurred_at: datetime
    reason_code: str | None


class MailboxReader(Protocol):
    @property
    def configured(self) -> bool: ...

    @property
    def identity(self) -> str: ...

    def fetch_messages(self, limit: int) -> list[MailboxMessage]: ...


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class IMAPMailboxReader:
    """Read a bounded mailbox window without changing message read/unread state."""

    def __init__(
        self,
        *,
        host: str | None = None,
        port: int | None = None,
        username: str | None = None,
        password: str | None = None,
        mailbox: str | None = None,
        use_ssl: bool | None = None,
        starttls: bool | None = None,
        timeout_seconds: int = 20,
    ) -> None:
        self.host = host if host is not None else os.environ.get("IMAP_HOST")
        self.use_ssl = use_ssl if use_ssl is not None else _env_bool("IMAP_USE_SSL", True)
        default_port = "993" if self.use_ssl else "143"
        self.port = port if port is not None else int(os.environ.get("IMAP_PORT", default_port))
        self.username = username if username is not None else os.environ.get("IMAP_USERNAME")
        self._password = password if password is not None else os.environ.get("IMAP_PASSWORD")
        self.mailbox = mailbox if mailbox is not None else os.environ.get("IMAP_MAILBOX", "INBOX")
        self.starttls = (
            starttls
            if starttls is not None
            else _env_bool("IMAP_STARTTLS", not self.use_ssl)
        )
        self.timeout_seconds = timeout_seconds

    @property
    def configured(self) -> bool:
        return bool(self.host and self.username and self._password)

    @property
    def identity(self) -> str:
        raw = f"{self.host or ''}|{self.username or ''}|{self.mailbox}".encode()
        return hashlib.sha256(raw).hexdigest()[:24]

    def fetch_messages(self, limit: int) -> list[MailboxMessage]:
        if not self.configured:
            raise MailboxNotConfigured("IMAP mailbox is not configured")
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")

        client: imaplib.IMAP4 | imaplib.IMAP4_SSL | None = None
        try:
            if self.use_ssl:
                client = imaplib.IMAP4_SSL(
                    self.host,
                    self.port,
                    ssl_context=ssl.create_default_context(),
                    timeout=self.timeout_seconds,
                )
            else:
                client = imaplib.IMAP4(
                    self.host,
                    self.port,
                    timeout=self.timeout_seconds,
                )
                if self.starttls:
                    client.starttls(ssl_context=ssl.create_default_context())

            client.login(self.username or "", self._password or "")
            status, _ = client.select(self.mailbox, readonly=True)
            if status != "OK":
                raise MailboxProviderError("IMAP mailbox could not be selected")

            status, data = client.uid("search", None, "ALL")
            if status != "OK":
                raise MailboxProviderError("IMAP mailbox search failed")
            uids = data[0].split() if data and data[0] else []
            selected = uids[-limit:]

            messages: list[MailboxMessage] = []
            for raw_uid in selected:
                status, payload = client.uid("fetch", raw_uid, "(RFC822)")
                if status != "OK":
                    continue
                raw = _raw_message(payload)
                if raw is None:
                    continue
                messages.append(
                    MailboxMessage(
                        uid=raw_uid.decode("ascii", errors="ignore"),
                        raw=raw,
                    )
                )
            return messages
        except MailboxProviderError:
            raise
        except (imaplib.IMAP4.error, OSError) as error:
            raise MailboxProviderError("IMAP mailbox request failed") from error
        finally:
            if client is not None:
                try:
                    client.logout()
                except (imaplib.IMAP4.error, OSError):
                    pass


class MailboxEngagementIngestor:
    """Translate mailbox replies and DSN failures into normalized engagement events."""

    def __init__(
        self,
        session: Session,
        reader: MailboxReader,
        *,
        now: datetime | None = None,
    ) -> None:
        self.session = session
        self.reader = reader
        self.now = now or datetime.now(UTC)

    def run(self, limit: int = 100) -> MailboxIngestionResult:
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        if not self.reader.configured:
            raise MailboxNotConfigured("IMAP mailbox is not configured")

        processed = matched = replies = bounces = skipped = 0
        service = ProviderEngagementIngestionService(self.session, now=self.now)

        for mailbox_message in self.reader.fetch_messages(limit):
            processed += 1
            outcome = parse_mailbox_outcome(mailbox_message.raw, now=self.now)
            if outcome is None:
                skipped += 1
                continue

            attempt = self._match_attempt(outcome.reference_message_ids)
            if attempt is None or not attempt.provider_message_id:
                skipped += 1
                continue

            provider_event_id = _mailbox_event_id(
                self.reader.identity,
                mailbox_message.uid,
                outcome.incoming_message_id,
            )
            try:
                service.ingest(
                    attempt.organization_id,
                    attempt.provider_key,
                    attempt.id,
                    provider_message_id=attempt.provider_message_id,
                    provider_event_id=provider_event_id,
                    event_type=outcome.event_type,
                    occurred_at=outcome.occurred_at,
                    reason_code=outcome.reason_code,
                )
            except (ConflictError, NotFoundError, ValueError):
                skipped += 1
                continue

            matched += 1
            if outcome.event_type == EngagementEventType.REPLIED:
                replies += 1
            elif outcome.event_type == EngagementEventType.BOUNCED:
                bounces += 1

        return MailboxIngestionResult(
            processed=processed,
            matched=matched,
            replies=replies,
            bounces=bounces,
            skipped=skipped,
        )

    def _match_attempt(
        self,
        reference_message_ids: tuple[str, ...],
    ) -> OutboundSendAttempt | None:
        if not reference_message_ids:
            return None
        attempts = list(
            self.session.scalars(
                select(OutboundSendAttempt).where(
                    OutboundSendAttempt.provider_key == "SMTP",
                    OutboundSendAttempt.status == OutboundSendStatus.SENT,
                    OutboundSendAttempt.provider_message_id.in_(reference_message_ids),
                )
            )
        )
        unique = {item.id: item for item in attempts}
        if len(unique) != 1:
            return None
        return next(iter(unique.values()))


def parse_mailbox_outcome(
    raw: bytes,
    *,
    now: datetime | None = None,
) -> ParsedMailboxOutcome | None:
    current = now or datetime.now(UTC)
    try:
        message = BytesParser(policy=policy.default).parsebytes(raw)
    except (TypeError, ValueError):
        return None

    incoming_message_id = _first_message_id(message.get("Message-ID"))
    occurred_at = _safe_message_date(message, current)
    dsn_failed, dsn_reason = _dsn_failure(message)
    if dsn_failed:
        references = _bounce_reference_ids(message)
        if not references:
            return None
        return ParsedMailboxOutcome(
            event_type=EngagementEventType.BOUNCED,
            reference_message_ids=references,
            incoming_message_id=incoming_message_id,
            occurred_at=occurred_at,
            reason_code=dsn_reason,
        )

    if _is_automatic_response(message):
        return None

    references = _reply_reference_ids(message)
    if not references:
        return None
    return ParsedMailboxOutcome(
        event_type=EngagementEventType.REPLIED,
        reference_message_ids=references,
        incoming_message_id=incoming_message_id,
        occurred_at=occurred_at,
        reason_code="MAILBOX_REPLY",
    )


def _raw_message(payload: list[object] | None) -> bytes | None:
    for item in payload or []:
        if isinstance(item, tuple) and len(item) >= 2:
            value = item[1]
            if isinstance(value, bytes):
                return value
            if isinstance(value, bytearray):
                return bytes(value)
    return None


def _reply_reference_ids(message: Message) -> tuple[str, ...]:
    return _unique_message_ids(
        _extract_message_ids(message.get("In-Reply-To"))
        + _extract_message_ids(message.get("References"))
    )


def _bounce_reference_ids(message: Message) -> tuple[str, ...]:
    values: list[str] = []
    values.extend(_extract_message_ids(message.get("In-Reply-To")))
    values.extend(_extract_message_ids(message.get("References")))

    for part in message.walk():
        values.extend(_extract_message_ids(part.get("Original-Message-ID")))
        values.extend(_extract_message_ids(part.get("X-Original-Message-ID")))
        if part.get_content_type() != "message/rfc822":
            continue
        payload = part.get_payload()
        if isinstance(payload, list):
            for embedded in payload:
                if isinstance(embedded, Message):
                    values.extend(_extract_message_ids(embedded.get("Message-ID")))
                    values.extend(_extract_message_ids(embedded.get("References")))
    return _unique_message_ids(values)


def _dsn_failure(message: Message) -> tuple[bool, str | None]:
    for part in message.walk():
        if part.get_content_type() != "message/delivery-status":
            continue
        payload = part.get_payload()
        blocks = payload if isinstance(payload, list) else []
        for block in blocks:
            if not isinstance(block, Message):
                continue
            action = (block.get("Action") or "").strip().lower()
            status = (block.get("Status") or "").strip()
            if action == "failed" or status.startswith("5."):
                if status:
                    safe_status = re.sub(r"[^0-9.]", "", status)[:20].replace(".", "_")
                    if safe_status:
                        return True, f"DSN_{safe_status}"
                return True, "DSN_FAILED"
    return False, None


def _is_automatic_response(message: Message) -> bool:
    auto_submitted = (message.get("Auto-Submitted") or "").strip().lower()
    if auto_submitted and auto_submitted != "no":
        return True
    return any(
        message.get(name) is not None
        for name in (
            "X-Autoreply",
            "X-Autorespond",
            "X-Auto-Response-Suppress",
        )
    )


def _safe_message_date(message: Message, fallback: datetime) -> datetime:
    value = message.get("Date")
    if not value:
        return fallback
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return fallback
    if parsed is None:
        return fallback
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    parsed = parsed.astimezone(UTC)
    if parsed > fallback + timedelta(days=1):
        return fallback
    return parsed


def _extract_message_ids(value: str | None) -> list[str]:
    if not value:
        return []
    return _MESSAGE_ID_RE.findall(str(value))


def _first_message_id(value: str | None) -> str | None:
    matches = _extract_message_ids(value)
    return matches[0] if matches else None


def _unique_message_ids(values: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        clean = value.strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
    return tuple(result)


def _mailbox_event_id(
    mailbox_identity: str,
    uid: str,
    incoming_message_id: str | None,
) -> str:
    raw = f"{mailbox_identity}|{uid}|{incoming_message_id or ''}".encode()
    return f"imap:{hashlib.sha256(raw).hexdigest()}"
