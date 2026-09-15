# Architecture and security

## Structure

MarketingIQ is an API-ready modular monolith:

- `domain` defines relational entities, controlled vocabularies and provider ports.
- `application` owns use cases and requires an explicit `TenantContext` for private records.
- `infrastructure` owns database configuration, seed helpers and later provider adapters.
- Future UI, REST handlers and background workers call application services rather than models
  or provider SDKs directly.

`CompanyService` is the tenant-scoped boundary for company relationships, facts, evidence, and
synchronous CSV imports. The authenticated internal API derives its `TenantContext` from the JWT
actor and organization membership; it never trusts an actor identifier supplied in a header.

MySQL 8 is the production system of record for the initial shared-hosting deployment. Background
work can initially use a database outbox/job table and a separate worker process from the same
codebase; select a queue only when load and delivery semantics are known. A public API is
deliberately not implemented yet.

## Multi-tenancy and company identity

`Company` and `CompanyIdentifier` form a global identity registry. Domains are normalized and
unique, allowing safe reuse without duplicating identity. `OrganizationCompany` is the
tenant-owned relationship containing lifecycle and private notes. Products, ICPs, memberships,
overrides, audit events and tenant research are explicitly keyed by `organization_id`.

Application access to tenant-owned data goes through tenant-scoped services/repositories built
with a non-empty tenant context; reads add the tenant predicate and writes reject mismatched
organizations. Future repositories must follow the same rule, and integration tests must prove
cross-tenant denial. MySQL does not provide PostgreSQL-style row-level security, so application
scoping, RBAC, least-privilege database credentials, auditability and tenant-isolation tests are
mandatory rather than optional defense in depth.

Global facts have a null `organization_id`. Customer-provided or tenant-derived private facts
carry an organization and must never be promoted to a global record implicitly. A later,
audited publication use case may promote eligible derived facts after licensing and privacy
checks. Global identity fields should contain only conservative, verified identity data;
uncertain attributes belong in facts.

## Provenance and classification

`CompanyFact` is an immutable observation identified by company, key, JSON typed value and
observation time. JSON is appropriate for heterogeneous *fact values*, not catalog structure;
repeatable product and ICP criteria are normalized rows. New observations are appended rather
than overwriting earlier claims, so sources may disagree and values may change. A later resolver
will select the current best-known value using recency, confidence, source policy and overrides.

Each fact records one controlled classification:

- `PUBLIC_EVIDENCE`
- `THIRD_PARTY_LICENSED`
- `CUSTOMER_PROVIDED`
- `MARKETINGIQ_DERIVED`

Redistribution is separately controlled by `UNKNOWN`, `ALLOWED`, `RESTRICTED` or
`INTERNAL_ONLY`; unknown is deny-by-default for future exports. `Evidence` links a fact to a
provider/source, reference, retrieval time and verification time. Derived facts can record a
model/rules version and retain evidence links. `HumanOverride` is append-only and tenant scoped.
Raw licensed data and MarketingIQ-derived facts remain separate rows and classifications.

## Provider abstraction

Capability-specific protocols cover `search_companies`, `enrich_company`, `search_contacts`,
`find_email` and `verify_email`. Future `HunterProvider`, `PeopleDataLabsProvider`,
`ApolloProvider`, `ClayProvider`, `PublicWebProvider`, `CSVProvider` and `ManualProvider`
adapters will implement only supported ports. Adapters translate external payloads into staged
provider results; application services validate, classify and persist them. No provider adapter
or API credential is implemented in Sprint 1.

## Security decisions

- Authentication will use a maintained web framework's password hasher and session/token
  facilities; plaintext passwords are never stored. `password_hash` is only the persistence slot.
- Super Admin is a platform-level `User.is_super_admin` flag, deliberately separate from
  `ORGANIZATION_ADMIN`, `MARKETING_USER` and `READ_ONLY` memberships. Authorization must check
  both tenant membership and action; super-admin access must be audited.
- HTTP entry points must add schema validation, size limits, generic error responses, output
  escaping, secure cookies and CSRF protection for cookie-authenticated mutations.
- Identifiers, uniqueness, foreign keys and confidence ranges have database constraints.
- Secrets come from environment/secret management. Provider payloads and credentials must not
  be logged. Audit logs record actor, tenant, action and target without secret values.
- Production MySQL connections require TLS when the hosting provider supports it, least-privilege
  database users, encrypted backups and tenant-aware restore/access procedures.

## Initial and future domain

Implemented: Organization, User, OrganizationMembership, Product and normalized criteria, ICP
and normalized criteria, Company, CompanyIdentifier, OrganizationCompany, CompanyFact, Evidence,
DataSource, ResearchRun, HumanOverride and AuditLog. Contacts, Leads, Campaigns and Opportunities
will be tenant-owned aggregates linked to the global company identity; they are intentionally
documented rather than prematurely implemented.

The company endpoints normalize and reuse shared domain identities, expose only global and active
tenant facts, restrict CSV imports to Organization Admins, and audit company, fact, evidence, and
import mutations. Only `MANUAL` and `CSV` data sources are accepted in Sprint 1.
