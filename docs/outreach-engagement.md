# Outreach Engagement Tracking

MarketingIQ records post-send outcomes as append-only tenant-private events linked to the exact outbound send attempt. Outcomes can be recorded manually by authorized tenant users or ingested through the normalized signed provider webhook endpoint.

Supported outcomes are `DELIVERED`, `BOUNCED`, `COMPLAINT`, `REPLIED`, `POSITIVE_REPLY`, `NEGATIVE_REPLY`, and `OPT_OUT`.

Manual events keep their tenant user as the recorder. Provider-ingested events use `source=PROVIDER`, keep provider key/event ID provenance, and deliberately use no tenant user actor. This prevents webhook processing from impersonating the employee who originally sent the campaign.

Each event has an organization-scoped idempotency key, source, optional normalized reason code, event time, and links to the company, draft, contact, contact email, and send attempt. Raw email bodies, recipient addresses, complete provider payloads, and credentials are not copied into engagement events or audits. Product attribution is retained as safe derived metadata from the linked draft so later MarketingIQ learning can analyze response patterns by product without copying provider datasets.

Marketing Users and Organization Admins can record manual outcomes. Read Only users can view history and summaries. Reusing the same manual event key is idempotent for the same attempt and event type and rejected otherwise.

`BOUNCED`, `COMPLAINT`, and `OPT_OUT` automatically create a tenant suppression entry when one does not already exist. Provider-created suppressions have no tenant user creator. No automatic unsuppression is provided.

The per-attempt summary reports delivery state, whether a reply occurred, reply sentiment, complaint/opt-out flags, event count, and latest event time. Positive and negative reply labels remain explicit human-recorded outcomes; provider ingestion accepts only `DELIVERED`, `BOUNCED`, `COMPLAINT`, `REPLIED`, and `OPT_OUT` and does not infer sentiment from private reply text.

## Manual API routes

- `POST .../send-attempts/{attempt_id}/engagements`
- `GET .../send-attempts/{attempt_id}/engagements`
- `GET .../send-attempts/{attempt_id}/engagements/summary`

## Normalized provider webhook

Provider-specific adapters or gateways can call:

`POST /api/v1/organizations/{org_id}/webhooks/outbound/{provider_key}/engagement`

The route intentionally does not use a tenant JWT. It authenticates the exact raw body with HMAC-SHA256 using `ENGAGEMENT_WEBHOOK_SECRET` and requires:

- `X-MarketingIQ-Webhook-Timestamp`: Unix epoch seconds.
- `X-MarketingIQ-Webhook-Signature`: lowercase/uppercase hex SHA-256 digest, optionally prefixed with `sha256=`.
- signature input: `timestamp + "." + exact_request_body`.
- request age within `ENGAGEMENT_WEBHOOK_MAX_AGE_SECONDS` (default 300, clamped to 30-3600 seconds).

The normalized JSON payload contains:

- `send_attempt_id`
- `provider_message_id`
- `provider_event_id`
- `event_type`
- `occurred_at`
- optional `reason_code`

MarketingIQ accepts an event only when the organization, send attempt, provider key, and provider message ID exactly match an existing `SENT` outbound attempt. `provider_event_id` is unique per organization/provider and makes provider retries idempotent. Reusing the same provider event ID for another attempt or event type is rejected.

The shared secret must contain at least 32 characters and is stored only in deployment environment configuration. If it is missing, webhook ingestion returns HTTP 503. Missing/invalid/expired signatures return HTTP 401.

This is a provider-neutral normalized contract, not a claim that generic SMTP supplies delivery/open/reply telemetry. Direct vendor adapters still need to translate and verify each ESP's native callback format before forwarding the normalized signed event. Generic SMTP remains send-only unless a separate provider or mailbox integration supplies outcome events.

Mailbox reply ingestion, provider-specific native webhook adapters, bounce mailbox parsing, automated sentiment analysis, automatic follow-ups, and open/click tracking remain separate increments. The normalized ingestion model allows those integrations to reuse the same append-only engagement history without rewriting prior events.
