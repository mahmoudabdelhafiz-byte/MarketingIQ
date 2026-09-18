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

- `APP_ENV=production`, so the deployed runtime uses production-safe API behavior;
- `DATABASE_URL` using `mysql+pymysql`, matching the current MySQL 8 shared-hosting target;
- `AUTH_SECRET` with at least 32 characters;
- rejection of the checked-in example database password and example development auth secret;
- an explicit database backup strategy: `provider_managed` or `operator_managed`;
- `DATABASE_BACKUP_RETENTION_DAYS` between 1 and 3650;
- a non-future `DATABASE_RESTORE_TEST_DATE` in `YYYY-MM-DD` format;
- a positive `DB_POOL_RECYCLE_SECONDS`;
- bounded cron batch sizes matching the application limits:
  - `AUTOMATION_CRON_BATCH_SIZE`: 1–100;
  - `PIPELINE_SYNC_BATCH_SIZE`: 1–500;
  - `MAILBOX_ENGAGEMENT_BATCH_SIZE`: 1–1000.

This is configuration validation only. It does not prove that the database exists, credentials
authenticate, DNS resolves, TLS certificates are valid, migrations have run, cron is installed,
or a declared backup actually exists. The backup settings are an explicit operational gate; follow
the database backup/restore runbook and perform a real restore rehearsal separately.

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

1. inject environment variables and secrets, including `APP_ENV=production`;
2. run the offline configuration preflight;
3. create or confirm the hosting MySQL database and dedicated user;
4. verify the declared backup strategy/retention and complete a restore rehearsal; record its date in `DATABASE_RESTORE_TEST_DATE`;
5. immediately before a production schema change, create or confirm a fresh recoverable backup and record the pre-migration Alembic revision;
6. keep the validated MySQL `DATABASE_URL` exported and run `alembic upgrade head` explicitly; Alembic has no checked-in fallback URL;
7. run `python -m marketingiq.jobs.verify_database_schema` and require a successful current-schema result;
8. start the API through `marketingiq.api.production:create_production_app` (for example with Uvicorn `--factory`) so production configuration is revalidated at process startup;
9. verify `/health/live` and `/health/ready`; readiness also requires the database schema to match the code's Alembic head;
10. configure cron jobs separately only for the bounded jobs that are intentionally enabled; each scheduled entrypoint uses a database-scoped zero-wait MySQL advisory lock to skip overlapping invocations;
11. verify SMTP/IMAP/provider connectivity through their controlled operational paths.

Passing the preflight is not a production deployment and does not execute migrations, install
cron jobs, enable outbound sending, or validate external-provider credentials.

## Recovery runbook

See [Database backup and restore readiness](database-backup-restore.md) before the first production
deployment and before any production migration. Passing preflight records that a recovery strategy
has been declared; it does not create or restore a backup.
