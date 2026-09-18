# MySQL shared-hosting deployment notes

MarketingIQ now targets MySQL 8 for the initial shared-hosting deployment.

## Required database configuration

Use a hosting-created MySQL database and a dedicated database user. Do not commit credentials.
Set the application connection string in the environment using this form:

```text
mysql+pymysql://USER:PASSWORD@HOST:3306/DATABASE?charset=utf8mb4
```

URL-encode special characters in `USER` and `PASSWORD`. Keep `charset=utf8mb4` so company,
contact, campaign, and evidence text can safely store full Unicode.

Recommended environment variables:

```text
DATABASE_URL=mysql+pymysql://USER:PASSWORD@HOST:3306/DATABASE?charset=utf8mb4
DB_POOL_RECYCLE_SECONDS=280
AUTH_SECRET=<at-least-32-random-characters>
```

`pool_pre_ping` is enabled automatically. The recycle interval is deliberately short because
shared MySQL services often close idle connections earlier than dedicated database servers.

## First deployment

Before connecting to production services, run the offline configuration preflight:

```bash
python -m marketingiq.jobs.validate_deployment
```

Alembic has no checked-in fallback database URL. Keep the production MySQL `DATABASE_URL`
exported in the same shell or hosting command environment used for migration execution. Migration
commands fail closed when `DATABASE_URL` is missing, malformed, or not `mysql+pymysql`.

Run the schema migration once against the hosting database before starting the application:

```bash
alembic upgrade head
python -m marketingiq.jobs.verify_database_schema
```

The verifier is read-only. It compares the database's Alembic revision with the migration head
shipped by the current code and exits non-zero when the database is unreachable, unmigrated, or
behind/ahead of the code's expected head. It never executes a migration.

Do not run development seed data automatically in production. A successful GitHub CI migration
check proves the migration chain against a clean MySQL 8 database, but it does not prove that a
specific hosting database has been migrated.

## Health checks

Expose the application health endpoints through the hosting platform or reverse proxy:

- `GET /health/live` returns HTTP 200 when the API process can serve requests.
- `GET /health/ready` verifies the configured database is reachable and its Alembic revision
  exactly matches the migration head shipped by the running code. It returns HTTP 200 only when
  both checks pass; otherwise it returns HTTP 503.

These endpoints require no authentication so infrastructure health probes can call them, and they
return only a coarse status without database URLs, credentials, exception text, migration revision
IDs, provider configuration, tenant data, or other operational details. Readiness does not verify
SMTP, IMAP, external research providers, or cron execution; those remain separate deployment
checks.

## Security

Use a dedicated MySQL user with access only to the MarketingIQ database. Enable TLS for the MySQL
connection if the hosting provider offers or requires it. Store the database password,
`AUTH_SECRET`, and provider API keys only in hosting environment variables or its secret facility.

MySQL does not provide the PostgreSQL row-level-security design previously considered for this
project. Tenant isolation therefore continues to rely on explicit `TenantContext`, tenant-scoped
queries, RBAC, audit logs, and integration tests.

## Existing PostgreSQL data

This database-backend change is not a PostgreSQL-to-MySQL data-copy utility. If a PostgreSQL
database ever contains data that must be retained, export/import and validation must be handled
as a separate migration exercise before cutover.
