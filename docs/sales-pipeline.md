# Sales Pipeline Foundation

MarketingIQ now links approved outreach and post-send engagement to a tenant-private sales opportunity.

## Opportunity lineage

Each opportunity is tied to the exact organization/company relationship, company, product, qualification, contact, campaign draft, and outbound send attempt that created the sales motion. One opportunity is allowed per tenant/send-attempt pair.

## Stages

The initial stage is `CONTACTED` after a successful send. If a reply engagement already exists when the opportunity is created, the initial stage is `RESPONDED`.

Supported stages are:

- `CONTACTED`
- `RESPONDED`
- `MEETING`
- `PROPOSAL`
- `WON`
- `LOST`

Stages may move forward, `LOST` may close any non-terminal opportunity, and `WON` requires `PROPOSAL`. Terminal opportunities cannot be reopened in this foundation increment.

## History and audit

Every stage change creates an append-only stage event with sequence number, previous stage, new stage, actor, timestamps, optional note, and optional reason code. The opportunity stores the current stage for fast reads, while the immutable event history remains the audit source.

Audit metadata intentionally stores IDs and stage values only; free-text notes, email addresses, message bodies, and provider payloads are excluded.

## RBAC

Organization Admin and Marketing User can create/update/move opportunities. Read Only can view opportunities and stage history but cannot modify them.

## Deferred

This increment does not add automatic opportunity creation, CRM/ERP synchronization, forecasting, probability scoring, aggregate conversion dashboards, AI win/loss inference, or automatic follow-up actions.
