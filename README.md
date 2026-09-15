# MarketingIQ

MarketingIQ is a multi-tenant B2B marketing-intelligence platform intended to turn
company evidence, product fit, buyer-role and campaign outcomes into reusable intelligence.
This repository contains the Sprint 1 company repository and an internal FastAPI adapter. It is
not a public/commercial API and does not perform automated research or provider enrichment.

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
pytest
ruff check .
```

Start the internal API after configuring `DATABASE_URL`:

```bash
fastapi run marketingiq.api.app:app
```

Requests use the selected organization in the route and the authenticated actor ID in the
`X-User-ID` header. This header is an adapter seam for the future authentication middleware,
not a production authentication mechanism. Every use case still verifies database membership
and role before reading or writing tenant data.

No secret or provider key belongs in Git. Development reads environment variables; hosted
environments should inject secrets from their native secret manager with separate credentials
per environment. Database roles should have only application-schema privileges.

## Documentation

- [Architecture, tenancy, provenance and security](docs/architecture.md)
- [Development workflow](docs/development-workflow.md)
- [Sprint boundaries](docs/sprint-boundaries.md)
- [Company repository and CSV import](docs/company-repository.md)
- [ADR 0001: modular monolith and shared company identity](docs/adr/0001-foundation.md)

## Seed data

`marketingiq.infrastructure.seed.seed_development_data(session)` explicitly and idempotently
creates the Barmageyat development tenant and four draft products. It is never run on import,
contains no personal data, and Barmageyat has no special behavior in the domain model.
