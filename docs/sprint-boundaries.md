# Sprint boundaries

## Sprint 1 foundation included

Relational tenancy and RBAC foundations, tenant/global company separation, product and versioned
ICP catalogs, append-only facts/evidence, provider ports, migrations, development seed data,
audit model and architecture/security documentation.

Task 3 adds `CompanyService`, strict domain normalization, safe `MANUAL`/`CSV` sources,
tenant-visible facts and evidence, duplicate-domain reuse, synchronous CSV preview/import,
import-specific RBAC, mutation audit logging, authenticated routes, tests, and operator docs.

## Intentionally deferred

- User interface
- Contacts, leads, campaigns, opportunities and outreach tracking
- Provider implementations (including Hunter, Apollo, Clay and Instantly)
- AI agents, automated company discovery and qualification/scoring engines
- Background queue selection, billing, commercial/public API and API-key management
- Advanced analytics, row-level-security policies and production deployment

The recommended next Sprint 1 task is a thin authenticated HTTP application with organization
selection, membership authorization and Product/ICP CRUD calling tenant-scoped application
services, plus PostgreSQL integration tests that exercise database constraints and future RLS.

## Sprint 1 Task 2 status

The internal HTTP adapter, local password authentication, explicit membership-to-`TenantContext`
authorization, and tenant-scoped Product, versioned ICP, and Company relationship use cases are now
implemented. Super Admin remains a distinct platform flag and receives no implicit organization
membership. External providers, AI research, campaigns, outreach, billing, and any commercial
public API remain outside this increment.

## Stabilization validation gate

Completed implementation includes the architecture and tenant/provenance models, internal
FastAPI and authentication foundations, tenant-scoped Product and versioned ICP CRUD, basic
shared Company association with private tenant relationships, and the initial RBAC matrix.

Authentication, API isolation, role-matrix, and PostgreSQL integration tests are retained in the
repository. Their execution status must be taken from the latest local run and CI result; merely
committing a test does not establish a pass. In environments without `TEST_DATABASE_URL`, the
PostgreSQL suite reports skips.

Still deferred: external providers, AI research, lead scoring, campaign generation,
automated outreach, billing, a commercial API, a frontend application, and production deployment.
