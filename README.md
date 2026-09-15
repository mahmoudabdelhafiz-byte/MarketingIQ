# MarketingIQ

MarketingIQ is a multi-tenant B2B marketing-intelligence platform intended to turn
company evidence, product fit, buyer-role and campaign outcomes into reusable intelligence.
The repository contains the Sprint 1 domain foundation and a thin authenticated internal API.

## Stack

The V1 architecture is a Python 3.12 modular monolith using SQLAlchemy 2, Alembic and
PostgreSQL. A single deployable keeps operations and hosting costs modest, while domain,
application and infrastructure modules keep future HTTP, worker and provider adapters from
owning business rules. SQLite is used only by isolated unit tests.

## Local setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env                 # replace every placeholder locally
export DATABASE_URL='postgresql+psycopg://...'
alembic upgrade head
uvicorn 'marketingiq.api.app:create_app' --factory
pytest
ruff check .
```

No secret or provider key belongs in Git. Development reads environment variables; hosted
environments should inject secrets from their native secret manager with separate credentials
per environment. Database roles should have only application-schema privileges.

## Documentation

- [Architecture, tenancy, provenance and security](docs/architecture.md)
- [Company repository, facts, evidence and CSV import](docs/company-repository.md)
- [Development workflow](docs/development-workflow.md)
- [Sprint boundaries](docs/sprint-boundaries.md)
- [ADR 0001: modular monolith and shared company identity](docs/adr/0001-foundation.md)

## Seed data

`marketingiq.infrastructure.seed.seed_development_data(session)` explicitly and idempotently
creates the Barmageyat development tenant and four draft products. It is never run on import,
contains no personal data, and Barmageyat has no special behavior in the domain model.

## Internal API and authentication

Routes are versioned beneath `/api/v1`; this is an internal adapter, not the public commercial API. `POST /api/v1/auth/login` exchanges an email/password for a short-lived HS256 bearer token, and `GET /api/v1/me` returns the safe current-user view. Passwords use pwdlib's recommended Argon2 hash and are never returned. `AUTH_SECRET` is required, must be at least 32 characters, and must be supplied by the environment/secret manager.

Tenant resources live below `/api/v1/organizations/{org_id}`. Authentication and organization membership are checked independently; a verified membership produces the `TenantContext` used by every catalog/company service query. A mismatching optional `X-Organization-ID` is rejected.

| Role | Read catalog/company data | Create/update products, ICPs, companies/facts | CSV company import |
| --- | --- | --- | --- |
| Organization Admin | Yes | Yes | Yes |
| Marketing User | Yes | Yes | No |
| Read Only | Yes | No | No |
| Super Admin | Platform role only | No implicit tenant access | No implicit tenant access |

Product routes support list/get/create/update and activation. ICP routes support list/get/create, immutable revision-on-update, and activation. Company routes reuse shared identity by normalized domain while tenant-private lifecycle state and notes remain isolated. Facts are append-only observations with provenance/classification and evidence. CSV import supports a non-mutating preview, deterministic validation, duplicate-domain reuse, and conservative shared-field updates. Meaningful writes emit `AuditLog` records without storing full CSV contents.

Deferred: registration, MFA, SSO/OAuth, password recovery, token revocation/refresh, organization administration, external providers, automated/public-web/AI research, contacts, campaigns, outreach, billing, commercial APIs, background jobs, RLS, and deployment.
