# cPanel Passenger deployment

MarketingIQ can run on cPanel's Python/Passenger application manager through a narrow WSGI
compatibility layer. The application itself remains FastAPI/ASGI; `passenger_wsgi.py` adapts the
guarded production ASGI application to the WSGI callable Passenger expects.

The current MarketingIQ API uses ordinary HTTP request/response routes and has no WebSocket routes.
If WebSockets are added later, use a native ASGI deployment instead of the WSGI adapter for those
features.

## cPanel application values

For the Barmageyat deployment shown in cPanel, use:

```text
Python version: 3.12.14
Application root: marketingiq
Application URL: marketingiq.barmageyat.net
Application startup file: passenger_wsgi.py
Application Entry point: application
```

The resulting application root is expected to be under the account home directory, for example
`/home/<cpanel-user>/marketingiq`. Use the exact home path shown by cPanel rather than assuming a
username.

## Repository files

The application root must contain the repository checkout, including:

```text
passenger_wsgi.py
pyproject.toml
src/
migrations/
alembic.ini
```

Install the project into the Python 3.12 virtual environment created by cPanel:

```bash
python -m pip install --upgrade pip
python -m pip install .
```

The project dependency set includes `a2wsgi`, which exposes the FastAPI application as the WSGI
callable named `application`.

## Startup behavior

Passenger imports `passenger_wsgi.py`. That file immediately calls the guarded
`marketingiq.api.production:create_production_app` factory before wrapping it with
`a2wsgi.ASGIMiddleware`.

This means Passenger startup fails closed unless the production deployment preflight is valid,
including:

- `APP_ENV=production`;
- a valid database URL and non-example auth secret;
- declared backup strategy and retention;
- a recorded restore rehearsal date;
- valid optional integration configuration when those integrations are enabled.

The Passenger entrypoint does not run Alembic migrations, create backups, install cron jobs, or send
outbound messages.

## Environment variables

Configure production environment variables through cPanel's Python application environment-variable
controls or another hosting-supported secret mechanism. Do not commit a production `.env` file.

Before application startup, the required core configuration includes:

```text
APP_ENV=production
DATABASE_URL=mysql+pymysql://USER:PASSWORD@HOST:3306/DATABASE?charset=utf8mb4
AUTH_SECRET=<at-least-32-random-characters>
DATABASE_BACKUP_STRATEGY=provider_managed
DATABASE_BACKUP_RETENTION_DAYS=7
DATABASE_RESTORE_TEST_DATE=YYYY-MM-DD
```

Do not enter a fake restore-test date merely to satisfy startup. Complete the restore rehearsal
first and record its actual successful date.

## Database compatibility gate

Do not run `alembic upgrade head` on the hosting database until its actual engine/version has been
identified with:

```sql
SELECT VERSION();
```

The current hosted CI migration chain is validated on MySQL 8.0.46. A MariaDB server may be
protocol-compatible through PyMySQL but must be validated separately before production migration.

## Verification

After cPanel reports the application as started and the database schema is current, verify:

```text
https://marketingiq.barmageyat.net/health/live
https://marketingiq.barmageyat.net/health/ready
```

Both should return HTTP 200. Production `/docs` should return 404.

The production security headers should still be present through Passenger because they are emitted
by the underlying FastAPI application.

## Native ASGI alternative

Where the host provides a persistent ASGI process, the preferred native entrypoint remains:

```bash
uvicorn 'marketingiq.api.production:create_production_app' --factory
```

The Passenger adapter exists specifically for WSGI-only shared-hosting environments.
