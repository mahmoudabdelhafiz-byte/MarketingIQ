from __future__ import annotations

import imaplib
import ssl
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketingiq.domain.engagement import MailboxEngagementCheckpoint
from marketingiq.infrastructure.mailbox_engagement import (
    IMAPMailboxReader,
    MailboxMessage,
    MailboxNotConfigured,
    MailboxProviderError,
    _raw_message,
)


class CheckpointedIMAPMailboxReader(IMAPMailboxReader):
    """Read new IMAP messages with a durable UID cursor and UIDVALIDITY reset handling."""

    def __init__(self, session: Session, **kwargs) -> None:
        super().__init__(**kwargs)
        self.session = session

    def fetch_messages(self, limit: int) -> list[MailboxMessage]:
        if not self.configured:
            raise MailboxNotConfigured("IMAP mailbox is not configured")
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")

        checkpoint = self._checkpoint()
        client: imaplib.IMAP4 | imaplib.IMAP4_SSL | None = None
        try:
            client = self._connect()
            client.login(self.username or "", self._password or "")
            status, _ = client.select(self.mailbox, readonly=True)
            if status != "OK":
                raise MailboxProviderError("IMAP mailbox could not be selected")

            uid_validity = self._uid_validity(client)
            status, data = client.uid("search", None, "ALL")
            if status != "OK":
                raise MailboxProviderError("IMAP mailbox search failed")
            all_uids = self._uids(data)

            reset = checkpoint is not None and checkpoint.uid_validity != uid_validity
            if checkpoint is None or reset:
                selected = all_uids[-limit:]
            else:
                last_uid = checkpoint.last_processed_uid
                unseen = [uid for uid in all_uids if last_uid is None or uid > last_uid]
                selected = unseen[:limit]

            messages: list[MailboxMessage] = []
            for uid in selected:
                raw_uid = str(uid).encode("ascii")
                status, payload = client.uid("fetch", raw_uid, "(RFC822)")
                if status != "OK":
                    break
                raw = _raw_message(payload)
                if raw is None:
                    break
                messages.append(MailboxMessage(uid=str(uid), raw=raw))

            if messages:
                self._save_checkpoint(uid_validity, int(messages[-1].uid))
            elif checkpoint is None or reset:
                self._save_checkpoint(uid_validity, None)

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

    def _connect(self) -> imaplib.IMAP4 | imaplib.IMAP4_SSL:
        if self.use_ssl:
            return imaplib.IMAP4_SSL(
                self.host,
                self.port,
                ssl_context=ssl.create_default_context(),
                timeout=self.timeout_seconds,
            )
        client = imaplib.IMAP4(
            self.host,
            self.port,
            timeout=self.timeout_seconds,
        )
        if self.starttls:
            client.starttls(ssl_context=ssl.create_default_context())
        return client

    @staticmethod
    def _uid_validity(client: imaplib.IMAP4 | imaplib.IMAP4_SSL) -> str:
        response = client.response("UIDVALIDITY")
        values = response[1] if response else None
        if not values or values[0] is None:
            raise MailboxProviderError("IMAP server did not provide UIDVALIDITY")
        raw = values[0]
        if isinstance(raw, bytes):
            value = raw.decode("ascii", errors="strict")
        else:
            value = str(raw)
        clean = value.strip()
        if not clean or len(clean) > 64:
            raise MailboxProviderError("IMAP server returned invalid UIDVALIDITY")
        return clean

    @staticmethod
    def _uids(data) -> list[int]:
        raw_values = data[0].split() if data and data[0] else []
        uids: list[int] = []
        for raw in raw_values:
            try:
                uid = int(raw)
            except (TypeError, ValueError):
                continue
            if uid > 0:
                uids.append(uid)
        return sorted(set(uids))

    def _checkpoint(self) -> MailboxEngagementCheckpoint | None:
        return self.session.scalar(
            select(MailboxEngagementCheckpoint).where(
                MailboxEngagementCheckpoint.mailbox_identity == self.identity
            )
        )

    def _save_checkpoint(self, uid_validity: str, last_uid: int | None) -> None:
        checkpoint = self._checkpoint()
        now = datetime.now(UTC)
        if checkpoint is None:
            self.session.add(
                MailboxEngagementCheckpoint(
                    mailbox_identity=self.identity,
                    uid_validity=uid_validity,
                    last_processed_uid=last_uid,
                    created_at=now,
                    updated_at=now,
                )
            )
            self.session.flush()
            return

        checkpoint.uid_validity = uid_validity
        checkpoint.last_processed_uid = last_uid
        checkpoint.updated_at = now
        self.session.flush()
