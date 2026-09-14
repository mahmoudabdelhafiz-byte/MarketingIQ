# ADR 0001: Modular monolith and shared company identity

- **Status:** Accepted
- **Date:** 2026-09-14

## Context

MarketingIQ needs low-cost delivery today and reusable intelligence, providers, jobs and an API
later. Tenant-private relationships must not leak into shared intelligence.

## Decision

Use a Python/SQLAlchemy/PostgreSQL modular monolith. Keep one global company identity and attach
tenant-owned relationships by organization. Store evidence-bearing facts as append-only
observations with independent classification and redistribution policy. Require explicit tenant
context in application repositories. Integrate providers behind capability ports.

## Consequences

One codebase and database simplify deployment and transactions. Module boundaries permit future
extraction only if operational evidence justifies it. Shared identity reduces duplicates, while
the relationship table and scoped facts prevent private attributes from contaminating global
records. Every new tenant-owned aggregate and query must carry and enforce `organization_id`;
code review and isolation tests are mandatory. PostgreSQL RLS remains planned defense in depth.
