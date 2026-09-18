# ADR 0002: MySQL production database for shared hosting

- **Status:** Accepted
- **Date:** 2026-09-16
- **Supersedes:** the PostgreSQL production-database choice in ADR 0001

## Context

MarketingIQ is planned to be deployed initially on shared hosting where MySQL is the available
managed relational database. Keeping PostgreSQL as the production dependency would require a
separate database service and add avoidable operating cost and deployment complexity during the
pilot stage.

## Decision

Use MySQL 8 as the production relational database through SQLAlchemy 2 and Alembic. Use the
pure-Python PyMySQL driver so deployment does not depend on compiling native database client
libraries on shared hosting. Production connection strings use `mysql+pymysql://` and `utf8mb4`.

The application remains database-portable at the SQLAlchemy layer where practical, and isolated
unit tests continue to use SQLite. Hosted CI must exercise both ORM schema creation and the full
Alembic upgrade chain against MySQL 8 before a database-related change is accepted.

MySQL does not provide PostgreSQL-style row-level security. Tenant isolation therefore remains an
application-layer invariant enforced by `TenantContext`, tenant predicates, RBAC, integration
tests, and least-privilege database credentials. Super Admin still receives no implicit tenant
membership.

Shared-hosting connections may be closed while idle, so the engine uses connection pre-ping and a
configurable recycle interval. Credentials remain environment-only and must never be committed.

## Consequences

The initial deployment can use the hosting provider's MySQL service without operating a separate
PostgreSQL instance. Database-specific tests and migration checks now target MySQL. Features that
would depend on PostgreSQL-only capabilities, including native row-level security, must not be
assumed by future designs.

Changing the backend does not migrate data from an existing PostgreSQL database. If such a
database later contains production data, a separate, verified data migration plan is required.
