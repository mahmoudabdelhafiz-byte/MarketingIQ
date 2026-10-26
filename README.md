# MarketingIQ

Lead qualification methodology and buyer-role configuration are documented in
[`docs/lead-qualification.md`](docs/lead-qualification.md).

The Sprint 2 company research/provider design, security policy, modes, and credential handling are
documented in [Provider research foundation](docs/provider-research.md).

MarketingIQ is a multi-tenant B2B marketing-intelligence platform intended to turn
company evidence, product fit, buyer-role and campaign outcomes into reusable intelligence.

The application covers the full guarded outbound workflow — company import and public research,
product/ICP fit scoring, lead qualification, contact discovery, grounded (optionally AI-assisted)
campaign drafting, human review and approval, SMTP sending with one-click unsubscribe, engagement
tracking, a sales pipeline and conversion learning — behind an authenticated, tenant-scoped API.
A dependency-free web interface for operators is served at `/` by the same application.

## Automatic company discovery

**Automation** in the web interface finds new companies matching a product's ICP through Hunter
Discover (free, no credits), on demand or on a schedule, adds them as `discovered`, and can run
research → fit → qualification on each. See [Company discovery](docs/company-discovery.md). Scheduled
rules need the cron lines in [cPanel Passenger deployment](docs/cpanel-passenger.md).

## Decision-maker discovery

For a qualified company, **Decision makers** finds the best 1–3 people for each of a product's
target personas: company website first (free), optional AI role checks, then — only with
explicit credit approval — one filtered Hunter search and email find/verify for the top people.
Every person has stored evidence, an employment state and a transparent score; emails are never
guessed and LinkedIn is never scraped. See [Decision-maker discovery](docs/decision-maker-discovery.md).

## Web interface and first administrator

There is no public sign-up. After migrations, create the first organization administrator from
the hosting shell (the password is read from the environment or prompted, never passed as an
argument):

```bash
MARKETINGIQ_ADMIN_PASSWORD='<at-least-12-characters>' \
  python -m marketingiq.jobs.create_admin --email you@example.com \
  --org-name "Barmageyat" --org-slug barmageyat
```

The command is idempotent; add `--reset-password` to change an existing user's password. Then
sign in at the application URL. Sign-in is throttled per email and per client address after
repeated failures, and response timing does not reveal whether an account exists.

## Stack

The V1 architecture is a Python 3.12 modular monolith using SQLAlchemy 2, Alembic and MySQL 8
through the pure-Python PyMySQL driver. This keeps the production database compatible with the
initial shared-hosting target without requiring native database client compilation. A single
deployable keeps operations and hosting costs modest, while domain, application and
infrastructure modules keep future HTTP, worker and provider adapters from owning business rules.
SQLite is used only by isolated unit tests.

## Local setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env                 # replace every placeholder locally
export DATABASE_URL='mysql+pymysql://USER:PASSWORD@HOST:3306/DATABASE?charset=utf8mb4'
alembic upgrade head
uvicorn 'marketingiq.api.app:create_app' --factory
pytest
ruff check .
```

Production should use the guarded ASGI factory
`marketingiq.api.production:create_production_app` after running migrations and schema
verification; see the deployment documentation for the exact sequence.

URL-encode special characters in database usernames/passwords before putting them in
`DATABASE_URL`. `DB_POOL_RECYCLE_SECONDS` defaults to 280 seconds to reduce stale-connection
errors common on shared MySQL hosting; `pool_pre_ping` is always enabled.

No secret or provider key belongs in Git. Development reads environment variables; hosted
environments should inject secrets from their native secret manager with separate credentials
per environment. Database users should have only the privileges required by the application
schema.

## Documentation

- [Architecture, tenancy, provenance and security](docs/architecture.md)
- [Development workflow](docs/development-workflow.md)
- [Deployment configuration preflight](docs/deployment-preflight.md)
- [cPanel Passenger deployment](docs/cpanel-passenger.md)
- [Deploying from GitHub Actions](docs/deployment-github-actions.md)
- [Database backup and restore readiness](docs/database-backup-restore.md)
- [Sprint boundaries](docs/sprint-boundaries.md)
- [Company repository and CSV import](docs/company-repository.md)
- [ADR 0001: modular monolith and shared company identity](docs/adr/0001-foundation.md)
- [ADR 0002: MySQL production database for shared hosting](docs/adr/0002-mysql-shared-hosting.md)

## Seed data

`marketingiq.infrastructure.seed.seed_development_data(session)` explicitly and idempotently
creates the Barmageyat development tenant and four draft products. It is never run on import,
contains no personal data, and Barmageyat has no special behavior in the domain model.

## Internal API and authentication

Routes are versioned beneath `/api/v1`; this is an internal adapter, not the public commercial API. `POST /api/v1/auth/login` exchanges an email/password for a short-lived HS256 bearer token, and `GET /api/v1/me` returns the safe current-user view. Passwords use pwdlib's recommended Argon2 hash and are never returned. `AUTH_SECRET` is required, must be at least 32 characters, and must be supplied by the environment/secret manager.

Tenant resources live below `/api/v1/organizations/{org_id}`. Authentication and organization membership are checked independently; a verified membership produces the `TenantContext` used by every catalog service query. A mismatching optional `X-Organization-ID` is rejected.

| Role | Read catalog | Create/update products, ICPs, company relationships |
| --- | --- | --- |
| Organization Admin | Yes | Yes |
| Marketing User | Yes | Yes |
| Read Only | Yes | No |
| Super Admin | Platform role only | No implicit tenant access |

Product routes support list/get/create/update and activation. ICP routes support list/get/create, immutable revision-on-update, and activation. Company routes find or create a shared identity by normalized domain, attach it to a tenant, and expose updates only for tenant-private relationship fields. Create/update actions emit an `AuditLog` in the same transaction.

Not yet implemented: self-service registration, MFA, SSO/OAuth, password recovery, token revocation/refresh, organization/user administration screens, public commercial APIs, and billing.
