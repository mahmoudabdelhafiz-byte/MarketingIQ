"""Settings with non-ASCII text survive a non-UTF-8 locale (cPanel Passenger, cron)."""

import os

import marketingiq
from marketingiq.infrastructure.outbound import SMTPEmailSender


def test_surrogate_escaped_settings_are_repaired(monkeypatch):
    name = "برمجيات Barmageyat"
    broken = name.encode("utf-8").decode("ascii", "surrogateescape")
    monkeypatch.setenv("SMTP_FROM_NAME", broken)
    monkeypatch.setenv("PLAIN_SETTING", "plain")

    marketingiq._repair_environment()

    assert os.environ["SMTP_FROM_NAME"] == name
    assert os.environ["PLAIN_SETTING"] == "plain"
    sender = SMTPEmailSender()
    assert sender.from_name == name
    sender.from_name.encode("utf-8")  # would raise "surrogates not allowed" before the fix
