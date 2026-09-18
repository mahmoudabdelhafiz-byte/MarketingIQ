# Deployment configuration preflight

MarketingIQ includes an offline configuration check intended to run before a shared-hosting
deployment or before enabling scheduled jobs and optional integrations.

Run:

```bash
python -m marketingiq.jobs.validate_deployment
```

The command reads environment variables, validates their structure, prints a small JSON summary,
and exits with status 1 when configuration is invalid. It does **not** connect to MySQL, SMTP,
IMAP, OpenAI, Hunter, or any other provider, and it never sends outbound traffic.

## Core checks

The preflight requires:

- `DATABASE_URL` using `mysql+pymysql`, matching the current MySQL 8 shared-hosting target;
- `AUTH_SECRET` with at least 32 characters;
- a positive `DB_POOL_RECYCLE_SECONDS`;
- bounded cron batch sizes matching the application limits:
  - `AUTOMATION_CRON_BATCH_SIZE`: 1–100;
  - `PIPELINE_SYNC_BATCH_SIZE`: 1–500;
  - `MAILBOX_ENGAGEMENT_BATCH_SIZE`: 1–1000.

This is configuration validation only. It does not prove that the database exists, credentials
authenticate, DNS resolves, TLS certificates are valid, migrations have run, or cron is installed.

## Optional integrations

Optional integrations remain disabled when their enabling credentials are blank.

When SMTP settings are present, the preflight validates the required host/sender fields, optional
username/password pairing, port range, boolean syntax, and mutually exclusive SSL/STARTTLS modes.

When IMAP settings are present, it validates host, username, password, mailbox, port range, boolean
syntax, and mutually exclusive SSL/STARTTLS modes.

When `OPENAI_API_KEY` is present, the campaign model must be non-empty and the configured timeout
must be greater than zero.

When `ENGAGEMENT_WEBHOOK_SECRET` is present, it must contain at least 32 characters and the
signature age window must be between 30 and 3600 seconds.

Hunter requires only the presence of its API key at this stage; the preflight deliberately does
not make a credit-consuming or authentication request.

## Output privacy

A successful result resembles:

```json
{"errors": [], "integrations": {"engagement_webhook": false, "hunter": false, "imap": false, "openai": false, "smtp": false}, "status": "ok"}
```

Errors mention environment variable names and validation rules only. The report never includes
database passwords, API keys, SMTP/IMAP passwords, webhook secrets, tenant data, or provider
responses.

## Deployment sequence

For the initial shared-hosting deployment, use this order:

1. inject environment variables and secrets;
2. run the offline configuration preflight;
3. create or confirm the hosting MySQL database and dedicated user;
4. run `alembic upgrade head` explicitly;
5. start the API and verify `/health/live` and `/health/ready`;
6. configure cron jobs separately only for the bounded jobs that are intentionally enabled;
7. verify SMTP/IMAP/provider connectivity through their controlled operational paths.

Passing the preflight is not a production deployment and does not execute migrations, install
cron jobs, enable outbound sending, or validate external-provider credentials.
