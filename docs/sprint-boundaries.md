# Sprint boundaries

## Sprint 1 foundation included

Relational tenancy and RBAC foundations, tenant/global company separation, product and versioned
ICP catalogs, append-only facts/evidence, provider ports, migrations, development seed data,
audit model and architecture/security documentation.

## Intentionally deferred

- Web framework, authentication endpoints and user interface
- Contacts, leads, campaigns, opportunities and outreach tracking
- Provider implementations (including Hunter, Apollo, Clay and Instantly)
- AI agents, automated company discovery and qualification/scoring engines
- Background queue selection, billing, commercial/public API and API-key management
- Advanced analytics, row-level-security policies and production deployment

The recommended next Sprint 1 task is a thin authenticated HTTP application with organization
selection, membership authorization and Product/ICP CRUD calling tenant-scoped application
services, plus PostgreSQL integration tests that exercise database constraints and future RLS.
