from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from marketingiq.domain.outbound import (
    OutboundMessage,
    OutboundProviderAuthenticationError,
    OutboundProviderError,
    OutboundProviderNotConfigured,
    OutboundProviderRejected,
    OutboundSendResult,
)


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class SMTPEmailSender:
    key = "SMTP"

    def __init__(
        self,
        *,
        host: str | None = None,
        port: int | None = None,
        username: str | None = None,
        password: str | None = None,
        from_email: str | None = None,
        from_name: str | None = None,
        use_ssl: bool | None = None,
        starttls: bool | None = None,
        timeout_seconds: int = 15,
    ) -> None:
        self.host = host if host is not None else os.environ.get("SMTP_HOST")
        self.port = port if port is not None else int(os.environ.get("SMTP_PORT", "587"))
        self.username = username if username is not None else os.environ.get("SMTP_USERNAME")
        self._password = password if password is not None else os.environ.get("SMTP_PASSWORD")
        self.from_email = (
            from_email if from_email is not None else os.environ.get("SMTP_FROM_EMAIL")
        )
        self.from_name = from_name if from_name is not None else os.environ.get("SMTP_FROM_NAME")
        self.use_ssl = use_ssl if use_ssl is not None else _env_bool("SMTP_USE_SSL", False)
        self.starttls = (
            starttls if starttls is not None else _env_bool("SMTP_STARTTLS", not self.use_ssl)
        )
        self.timeout_seconds = timeout_seconds

    @property
    def configured(self) -> bool:
        return bool(self.host and self.from_email)

    def send(self, message: OutboundMessage) -> OutboundSendResult:
        if not self.configured:
            raise OutboundProviderNotConfigured("SMTP is not configured")

        email = EmailMessage()
        email["From"] = formataddr((self.from_name or "", self.from_email or ""))
        email["To"] = message.recipient
        email["Subject"] = message.subject
        email["Message-ID"] = make_msgid()
        email["X-MarketingIQ-Idempotency-Key"] = message.idempotency_key
        email.set_content(message.body)

        try:
            if self.use_ssl:
                client = smtplib.SMTP_SSL(
                    self.host,
                    self.port,
                    timeout=self.timeout_seconds,
                    context=ssl.create_default_context(),
                )
            else:
                client = smtplib.SMTP(self.host, self.port, timeout=self.timeout_seconds)
            with client:
                client.ehlo()
                if self.starttls and not self.use_ssl:
                    client.starttls(context=ssl.create_default_context())
                    client.ehlo()
                if self.username:
                    client.login(self.username, self._password or "")
                refused = client.send_message(email)
                if refused:
                    raise OutboundProviderRejected("SMTP recipient was rejected")
        except smtplib.SMTPAuthenticationError as error:
            raise OutboundProviderAuthenticationError("SMTP authentication failed") from error
        except (smtplib.SMTPRecipientsRefused, smtplib.SMTPDataError) as error:
            raise OutboundProviderRejected("SMTP message was rejected") from error
        except OutboundProviderError:
            raise
        except (smtplib.SMTPException, OSError) as error:
            raise OutboundProviderError("SMTP delivery request failed") from error

        return OutboundSendResult(
            provider_key=self.key,
            accepted=True,
            provider_message_id=str(email["Message-ID"]),
        )
