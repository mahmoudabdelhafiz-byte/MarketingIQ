# Sales Pipeline

MarketingIQ links approved outreach and post-send engagement to a tenant-private sales opportunity.

## Opportunity lineage

Each opportunity is tied to the exact organization/company relationship, company, product, qualification, contact, campaign draft, and outbound send attempt that created the sales motion. One opportunity is allowed per tenant/send-attempt pair.

## Automatic synchronization

`python -m marketingiq.jobs.run_pipeline_sync` is the bounded, cron-safe synchronization entrypoint. `PIPELINE_SYNC_BATCH_SIZE` defaults to 100 and may be set from 1 to 500. The command takes a zero-wait MySQL advisory lock scoped to the configured MarketingIQ database; an overlapping invocation exits successfully with `status=skipped` and `reason=already_running` instead of racing the same batch.

The synchronization performs only two deterministic actions:

- create a missing opportunity for a successful `SENT` outbound attempt;
- move an existing `CONTACTED` opportunity to `RESPONDED` when MarketingIQ has an observed reply engagement (`REPLIED`, `POSITIVE_REPLY`, or `NEGATIVE_REPLY`).

It is idempotent and never moves `MEETING`, `PROPOSAL`, `WON`, or `LOST` backward or sideways. It does not infer meetings, proposals, wins, losses, revenue, probability, or intent.

Historical sent attempts are eligible for backfill. If a reply already exists before the first sync, the newly created opportunity may start directly at `RESPONDED` while preserving the original send-attempt lineage.

## Stages

Supported stages are:

- `CONTACTED`
- `RESPONDED`
- `MEETING`
- `PROPOSAL`
- `WON`
- `LOST`

Manual stages may move forward, `LOST` may close any non-terminal opportunity, and `WON` requires `PROPOSAL`. Terminal opportunities cannot be reopened.

## History and audit

Every stage change creates an append-only stage event with sequence number, previous stage, new stage, source, actor when applicable, timestamps, optional note, and optional reason code. Event source is `MANUAL` or `SYSTEM`.

System-created stage events may have no user actor. This is intentional for provider/mailbox-observed replies and prevents MarketingIQ from falsely attributing a machine-observed event to a tenant user. An automatically created opportunity remains owned by the user who initiated the original outbound send.

Audit metadata intentionally stores IDs, stage values, and source only; free-text notes, email addresses, message bodies, mailbox contents, and provider payloads are excluded.

## RBAC

Organization Admin and Marketing User can manually create/update/move opportunities. Read Only can view opportunities and stage history but cannot modify them. The cron sync is a system process and does not bypass tenant boundaries; it derives organization scope from the linked outbound attempt and engagement records.

## Deferred

This increment does not add CRM/ERP synchronization, forecasting, probability scoring, autonomous stage inference beyond observed replies, automatic follow-up actions, or AI win/loss inference.
