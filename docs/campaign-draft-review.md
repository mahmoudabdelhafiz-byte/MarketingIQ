# Campaign Draft Review

MarketingIQ keeps outreach human-controlled. Generated campaign drafts are immutable source artifacts; edits and review decisions are stored as append-only review events rather than overwriting generated copy.

## Workflow

1. Generate a grounded campaign draft from a current actionable qualification and verified business contact.
2. A Marketing User or Organization Admin may edit the subject, body, or call to action.
3. Each edit snapshots the complete effective message as a new review revision with `DRAFT` status.
4. A reviewer may reject a draft with a reason. A rejected draft must be edited before it can be approved.
5. Approval snapshots the exact approved content and revalidates the qualification freshness, buyer-role alignment, and verified business email at approval time.
6. Approval does **not** send, schedule, or queue the message. Sending remains a separate future workflow.

## Immutability and history

`CampaignDraft` remains the original generated artifact. `CampaignDraftReviewEvent` records ordered human actions (`EDIT`, `APPROVE`, `REJECT`) with the exact content reviewed at each revision. The effective API view is derived from the latest event while `generated_content` preserves the original generated copy for comparison.

An approved draft cannot be edited or rejected in this increment. A rejected draft can return to `DRAFT` only through a new edit event. This avoids silent changes after approval and keeps a clear review trail.

## Permissions

- Read Only: view drafts and review history.
- Marketing User: generate, edit, approve, and reject drafts.
- Organization Admin: same campaign permissions plus existing administrative capabilities.

All lookups remain organization- and relationship-scoped.

## Audit and privacy

Review audit events contain safe identifiers, action metadata, and revision numbers. They do not include message body, subject, recipient email address, provider payloads, or API credentials. The review-event content itself is tenant-private operational data and is not exposed as redistributable provider intelligence.

## Approval freshness gate

Approval reuses the canonical fit-freshness path through the qualification relationship. If the underlying assessment is no longer current, approval is blocked with `CAMPAIGN_DRAFT_REQUALIFICATION_REQUIRED`; MarketingIQ does not silently requalify or approve stale copy.

## MySQL shared-hosting compatibility

The review history is implemented with ordinary MySQL-compatible tables, foreign keys, indexes, and string-backed enums. Alembic revision `20260916_09` follows the existing MySQL baseline. No PostgreSQL-only feature, background queue, or database-side row-level security is required.
