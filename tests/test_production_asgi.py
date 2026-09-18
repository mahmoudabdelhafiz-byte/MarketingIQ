import pytest
from fastapi.testclient import TestClient

from marketingiq.api.production import create_production_app


def _valid_production_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv(
        "DATABASE_URL",
        "mysql+pymysql://marketingiq:secret@db.example/marketingiq?charset=utf8mb4",
    )
    monkeypatch.setenv("AUTH_SECRET", "a-secure-production-secret-that-is-long-enough")
    monkeypatch.setenv("DATABASE_BACKUP_STRATEGY", "provider_managed")
    monkeypatch.setenv("DATABASE_BACKUP_RETENTION_DAYS", "7")
    monkeypatch.setenv("DATABASE_RESTORE_TEST_DATE", "2026-09-01")
    monkeypatch.setenv("DB_POOL_RECYCLE_SECONDS", "280")
    monkeypatch.setenv("AUTOMATION_CRON_BATCH_SIZE", "20")
    monkeypatch.setenv("PIPELINE_SYNC_BATCH_SIZE", "100")
    monkeypatch.setenv("MAILBOX_ENGAGEMENT_BATCH_SIZE", "100")
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


def test_production_asgi_factory_requires_valid_deployment_configuration(monkeypatch):
    _valid_production_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "staging")

    with pytest.raises(
        RuntimeError,
        match="APP_ENV must be production for deployment preflight",
    ):
        create_production_app()


def test_production_asgi_factory_rejects_example_secret_without_echoing_it(monkeypatch):
    _valid_production_env(monkeypatch)
    example_secret = "replace-with-a-random-development-value"
    monkeypatch.setenv("AUTH_SECRET", example_secret)

    with pytest.raises(RuntimeError) as error:
        create_production_app()

    assert "AUTH_SECRET must not use the example development value" in str(error.value)
    assert example_secret not in str(error.value)


def test_production_asgi_factory_builds_hardened_app(monkeypatch):
    _valid_production_env(monkeypatch)

    app = create_production_app()
    client = TestClient(app)

    assert app.state.environment == "production"
    assert client.get("/health/live").status_code == 200
    assert client.get("/docs").status_code == 404
    response = client.get("/health/live")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-frame-options"] == "DENY"
