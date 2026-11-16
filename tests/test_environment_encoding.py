"""Settings with non-ASCII text survive a non-UTF-8 locale (cPanel Passenger, cron)."""

import os

import pytest

from marketingiq.infrastructure.env import env_text, repair_text
from marketingiq.infrastructure.mailbox_engagement import IMAPMailboxReader
from marketingiq.infrastructure.outbound import SMTPEmailSender


def ascii_locale(value: str) -> str:
    """What Python's ASCII locale makes of a UTF-8 environment value."""
    return value.encode("utf-8").decode("ascii", "surrogateescape")


@pytest.fixture
def server_environ(monkeypatch):
    """os.environ as Passenger's ASCII-locale Python sees it (a UTF-8 test run cannot)."""
    fake: dict[str, str] = {}
    monkeypatch.setattr(os, "environ", fake)
    return fake


def test_sender_name_with_en_dash_and_arabic_is_read_correctly(server_environ):
    for name in ("Sales – Barmageyat", "مبيعات برمجيات"):
        server_environ["SMTP_FROM_NAME"] = ascii_locale(name)
        assert os.environ["SMTP_FROM_NAME"] != name  # really broken as on the server
        sender = SMTPEmailSender()
        assert sender.from_name == name
        sender.from_name.encode("utf-8")  # raised "surrogates not allowed" before the fix


def test_plain_and_missing_values_are_unchanged(server_environ):
    server_environ["SMTP_HOST"] = "mail.example.test"
    assert env_text("SMTP_HOST") == "mail.example.test"
    assert env_text("SMTP_FROM_NAME") is None
    assert env_text("SMTP_FROM_NAME", "x") == "x"


def test_invalid_bytes_are_replaced_not_crashing():
    broken = b"Sales \xff".decode("ascii", "surrogateescape")
    assert repair_text(broken) == "Sales �"


def test_imap_credentials_are_repaired(server_environ):
    server_environ["IMAP_USERNAME"] = ascii_locale("مبيعات@example.test")
    assert IMAPMailboxReader().username == "مبيعات@example.test"


def test_email_address_as_smtp_host_is_not_configured():
    sender = SMTPEmailSender(host="someone@example.test", from_email="sales@example.test")
    assert sender.configured is False
    assert SMTPEmailSender(host="mail.example.test", from_email="sales@example.test").configured
