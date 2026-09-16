# Outreach Engagement Tracking

MarketingIQ records post-send outcomes as append-only tenant-private events linked to the exact outbound send attempt. This increment captures outcomes; it does not add autonomous follow-ups or provider webhooks.

Supported outcomes are `DELIVERED`, `BOUNCED`, `COMPLAINT`, `REPLIED`, `POSITIVE_REPLY`, `NEGATIVE_REPLY`, and `OPT_OUT`.

Each event has an organization-scoped idempotency key, source, optional normalized reason code, event time, actor, and links to the company, draft, contact, contact email, and send attempt. Raw email bodies, recipient addresses, provider payloads, and credentials are not copied into engagement events or audits. Product attribution is retained as safe derived metadata from the linked draft so later MarketingIQ learning can analyze response patterns by product without copying provider datasets.

Marketing Users and Organization Admins can record outcomes. Read Only users can view history and summaries. Reusing the same event key is idempotent for the same attempt and event type and rejected otherwise.

`BOUNCED`, `COMPLAINT`, and `OPT_OUT` automatically create a tenant suppression entry when one does not already exist. This prevents later sends to the same business address. No automatic unsuppression is provided.

The per-attempt summary reports delivery state, whether a reply occurred, reply sentiment, complaint/opt-out flags, event count, and latest event time. Positive and negative reply labels are explicit recorded outcomes; MarketingIQ does not infer sentiment from private reply text in this increment.

API routes are nested below the exact campaign draft and send attempt:

- `POST .../send-attempts/{attempt_id}/engagements`
- `GET .../send-attempts/{attempt_id}/engagements`
- `GET .../send-attempts/{attempt_id}/engagements/summary`

Provider delivery/open/click webhooks, mailbox reply ingestion, bounce mailbox parsing, automated sentiment analysis, automatic follow-ups, and aggregate conversion dashboards remain deferred. The data model is intended to support those later without rewriting historical events.
