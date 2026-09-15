# Sprint boundaries

## Sprint 1 included

Relational tenancy and RBAC foundations, tenant/global company separation, product and versioned
ICP catalogs, append-only facts/evidence, provider ports, migrations, development seed data,
audit model and architecture/security documentation. The completed company-repository increment
adds tenant-scoped manual company attachment and relationship updates, internal/CSV data sources,
append-only tenant facts and evidence, synchronous CSV preview/import, write auditing, API routes,
and unit/integration-style service tests.

## Intentionally deferred

- Authentication endpoints/middleware and user interface (the internal FastAPI adapter accepts an
  actor-header seam and always validates membership; it is not ready for public exposure)
- Contacts, leads, campaigns, opportunities and outreach tracking
- Provider implementations (including Hunter, Apollo, Clay and Instantly)
- AI agents, automated company discovery and qualification/scoring engines
- Background queue selection, billing, commercial/public API and API-key management
- Advanced analytics, row-level-security policies and production deployment

The recommended Sprint 2 task is production authentication middleware and organization selection,
followed by Product/ICP CRUD and PostgreSQL row-level-security defense-in-depth. Provider-backed,
public-web and AI research remain explicitly out of scope until those controls are complete.
