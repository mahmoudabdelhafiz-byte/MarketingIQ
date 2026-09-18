from marketingiq.infrastructure.deployment_config import validate_deployment_environment


def _valid_env() -> dict[str, str]:
    return {
        "DATABASE_URL": "mysql+pymysql://marketingiq:secret@db.example/marketingiq?charset=utf8mb4",
        "AUTH_SECRET": "a-secure-production-secret-that-is-long-enough",
    }


def test_minimal_shared_hosting_config_is_valid():
    report = validate_deployment_environment(_valid_env())

    assert report.ok is True
    assert report.errors == ()
    assert report.integrations == {
        "smtp": False,
        "imap": False,
        "openai": False,
        "hunter": False,
        "engagement_webhook": False,
    }


def test_core_database_and_batch_settings_are_validated():
    env = {
        **_valid_env(),
        "DATABASE_URL": "sqlite+pysqlite:///marketingiq.db",
        "AUTH_SECRET": "short",
        "DB_POOL_RECYCLE_SECONDS": "0",
        "AUTOMATION_CRON_BATCH_SIZE": "101",
        "PIPELINE_SYNC_BATCH_SIZE": "invalid",
        "MAILBOX_ENGAGEMENT_BATCH_SIZE": "1001",
    }

    report = validate_deployment_environment(env)

    assert report.ok is False
    assert "DATABASE_URL must use mysql+pymysql for shared-hosting deployment" in report.errors
    assert "AUTH_SECRET must contain at least 32 characters" in report.errors
    assert "DB_POOL_RECYCLE_SECONDS must be at least 1" in report.errors
    assert "AUTOMATION_CRON_BATCH_SIZE must be between 1 and 100" in report.errors
    assert "PIPELINE_SYNC_BATCH_SIZE must be an integer" in report.errors
    assert "MAILBOX_ENGAGEMENT_BATCH_SIZE must be between 1 and 1000" in report.errors


def test_partial_smtp_configuration_is_rejected():
    env = {
        **_valid_env(),
        "SMTP_HOST": "mail.example.com",
        "SMTP_FROM_EMAIL": "marketing@example.com",
        "SMTP_USERNAME": "marketing@example.com",
        "SMTP_USE_SSL": "true",
        "SMTP_STARTTLS": "true",
    }

    report = validate_deployment_environment(env)

    assert report.integrations["smtp"] is True
    assert "SMTP_USERNAME and SMTP_PASSWORD must be configured together" in report.errors
    assert "SMTP_USE_SSL and SMTP_STARTTLS cannot both be true" in report.errors


def test_partial_imap_and_invalid_boolean_configuration_are_rejected():
    env = {
        **_valid_env(),
        "IMAP_HOST": "mail.example.com",
        "IMAP_USERNAME": "marketing@example.com",
        "IMAP_USE_SSL": "sometimes",
        "IMAP_PORT": "70000",
    }

    report = validate_deployment_environment(env)

    assert report.integrations["imap"] is True
    assert "IMAP_PASSWORD is required when its integration is enabled" in report.errors
    assert "IMAP_USE_SSL must be a boolean value" in report.errors
    assert "IMAP_PORT must be between 1 and 65535" in report.errors


def test_optional_integrations_are_reported_without_exposing_secret_values():
    env = {
        **_valid_env(),
        "OPENAI_API_KEY": "openai-secret-value",
        "CAMPAIGN_AI_MODEL": "gpt-5.6-luna",
        "CAMPAIGN_AI_TIMEOUT_SECONDS": "30",
        "HUNTER_API_KEY": "hunter-secret-value",
        "ENGAGEMENT_WEBHOOK_SECRET": "webhook-secret-that-is-at-least-32-characters",
        "ENGAGEMENT_WEBHOOK_MAX_AGE_SECONDS": "300",
    }

    report = validate_deployment_environment(env)
    rendered = str(report.as_dict())

    assert report.ok is True
    assert report.integrations["openai"] is True
    assert report.integrations["hunter"] is True
    assert report.integrations["engagement_webhook"] is True
    assert "openai-secret-value" not in rendered
    assert "hunter-secret-value" not in rendered
    assert "webhook-secret-that-is-at-least-32-characters" not in rendered
