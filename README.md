# MarketingIQ

MarketingIQ is a multi-tenant B2B marketing-intelligence platform intended to turn
company evidence, product fit, buyer-role and campaign outcomes into reusable intelligence.
This repository currently contains **Sprint 1 foundation only**, not a runnable web product.

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

No secret or provider key belongs in Git. Development reads environment variables; hosted
environments should inject secrets from their native secret manager with separate credentials
per environment. Database roles should have only application-schema privileges.

## Documentation

- [Architecture, tenancy, provenance and security](docs/architecture.md)
- [Development workflow](docs/development-workflow.md)
- [Sprint boundaries](docs/sprint-boundaries.md)
- [ADR 0001: modular monolith and shared company identity](docs/adr/0001-foundation.md)

## Seed data

`marketingiq.infrastructure.seed.seed_development_data(session)` explicitly and idempotently
creates the Barmageyat development tenant and four draft products. It is never run on import,
contains no personal data, and Barmageyat has no special behavior in the domain model.
