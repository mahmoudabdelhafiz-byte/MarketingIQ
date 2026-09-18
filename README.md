# MarketingIQ

Lead qualification methodology and buyer-role configuration are documented in
[`docs/lead-qualification.md`](docs/lead-qualification.md).

The Sprint 2 company research/provider design, security policy, modes, and credential handling are
documented in [Provider research foundation](docs/provider-research.md).

MarketingIQ is a multi-tenant B2B marketing-intelligence platform intended to turn
company evidence, product fit, buyer-role and campaign outcomes into reusable intelligence.
The repository contains the Sprint 1 domain foundation and a thin authenticated internal API.

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

Deferred: registration, MFA, SSO/OAuth, password recovery, token revocation/refresh, organization administration, public APIs, providers, research, campaigns, outreach, billing, and AI features.
