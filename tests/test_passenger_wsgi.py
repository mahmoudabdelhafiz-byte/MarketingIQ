from __future__ import annotations

import importlib.util
from pathlib import Path
from wsgiref.util import setup_testing_defaults

import pytest


def _configure_valid_production_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv(
        "DATABASE_URL",
        "mysql+pymysql://marketingiq:secret@db.example/marketingiq?charset=utf8mb4",
    )
    monkeypatch.setenv("AUTH_SECRET", "a-secure-production-secret-that-is-long-enough")
    monkeypatch.setenv("DATABASE_BACKUP_STRATEGY", "provider_managed")
    monkeypatch.setenv("DATABASE_BACKUP_RETENTION_DAYS", "7")
    monkeypatch.setenv("DATABASE_RESTORE_TEST_DATE", "2025-01-01")

    for name in (
        "SMTP_HOST",
        "SMTP_USERNAME",
        "SMTP_PASSWORD",
        "SMTP_FROM_EMAIL",
        "IMAP_HOST",
        "IMAP_USERNAME",
        "IMAP_PASSWORD",
        "OPENAI_API_KEY",
        "HUNTER_API_KEY",
        "ENGAGEMENT_WEBHOOK_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)


def _load_wsgi_module(name: str):
    path = Path(__file__).resolve().parents[1] / "marketingiq_wsgi.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_wsgi_entrypoint_fails_closed_on_invalid_production_config(monkeypatch):
    _configure_valid_production_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "staging")

    with pytest.raises(
        RuntimeError,
        match="APP_ENV must be production for deployment preflight",
    ):
        _load_wsgi_module("marketingiq_wsgi_invalid")


def test_wsgi_adapter_is_created_lazily(monkeypatch):
    _configure_valid_production_env(monkeypatch)
    module = _load_wsgi_module("marketingiq_wsgi_lazy")

    assert module._wsgi_app is None


def test_wsgi_entrypoint_serves_hardened_health_endpoint(monkeypatch):
    _configure_valid_production_env(monkeypatch)
    module = _load_wsgi_module("marketingiq_wsgi_valid")

    assert module._wsgi_app is None

    environ: dict[str, object] = {}
    setup_testing_defaults(environ)
    environ.update(
        {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": "/health/live",
            "QUERY_STRING": "",
            "SERVER_NAME": "marketingiq.barmageyat.net",
            "SERVER_PORT": "443",
            "wsgi.url_scheme": "https",
        }
    )

    captured: dict[str, object] = {}

    def start_response(status, headers, exc_info=None):
        captured["status"] = status
        captured["headers"] = dict(headers)
        return None

    body = b"".join(module.application(environ, start_response))

    assert module._wsgi_app is not None
    assert str(captured["status"]).startswith("200")
    headers = {str(k).lower(): str(v) for k, v in dict(captured["headers"]).items()}
    assert headers["cache-control"] == "no-store"
    assert headers["x-frame-options"] == "DENY"
    assert b'"status":"ok"' in body
