# Outbound Sending Foundation

MarketingIQ sends email only after an explicit authenticated user action. This increment does not add autonomous outreach, scheduling, sequences, background workers, or bulk-send automation.

## Safety gates

A send is eligible only when all of the following are true:

- the campaign draft belongs to the tenant and company relationship;
- the latest review event is an explicit human `APPROVE` event;
- the linked Lead Qualification is still actionable (`HIGH_PRIORITY` or `QUALIFIED`);
- the underlying fit assessment is still current;
- the contact is still aligned to the approved buyer role;
- the contact has a business email whose verification state is `VALID`;
- the recipient is not present in the tenant suppression list;
- the draft has not already been successfully sent.

A stale qualification is never silently refreshed. The user must re-run the earlier workflow and approve a new/current draft before sending.

## Unsubscribe and suppression

Every sent message carries a signed one-click unsubscribe link, both in an RFC 2369/8058
`List-Unsubscribe` + `List-Unsubscribe-Post: List-Unsubscribe=One-Click` header pair and as a short
footer in the body. The link token carries the tenant ID and recipient address (encoded, not
encrypted — the link is only ever sent to that recipient) plus an HMAC signature keyed from
`AUTH_SECRET`, so it needs no database lookup and cannot be altered to target another tenant or
address. The confirmation page shows only a masked form of the address.

`GET /unsubscribe?t=…` shows a confirmation page; `POST /unsubscribe?t=…` (the page's button, or a
mail client's one-click request) records an idempotent tenant-scoped `OPT_OUT` suppression, which
the send gate above then enforces. `MARKETINGIQ_PUBLIC_URL` must be set to the application's
public https URL whenever SMTP is enabled; production preflight refuses to start otherwise.

## Human action and idempotency

`POST .../campaign-drafts/{draft_id}/send` requires `SEND_OUTBOUND_EMAIL`. Marketing Users and Organization Admins have this permission; Read Only users do not.

Every request includes an explicit caller-generated idempotency key. Repeating the same key for the same draft returns the original immutable send attempt without contacting the provider again. Reusing the key for another draft is rejected. After one successful send, a different key cannot be used to send the same draft again. This deliberately favors duplicate-send prevention in the MVP.

## Provider abstraction

The application depends on the provider-neutral `EmailSender` contract. The initial adapter is `SMTPEmailSender`, which works with ordinary authenticated SMTP accounts, including typical shared-hosting mailboxes.

Environment variables:

- `SMTP_HOST`
- `SMTP_PORT` (default `587`)
- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `SMTP_FROM_EMAIL`
- `SMTP_FROM_NAME`
- `SMTP_USE_SSL` (default `false`)
- `SMTP_STARTTLS` (default `true` when SSL is disabled)

SMTP is considered configured only when at least the host and sender address exist. Credentials are never stored in the database or audit log.

## Shared-hosting design

The first implementation is synchronous by design: a human clicks Send and the SMTP adapter performs one delivery request immediately. This avoids introducing a persistent worker or queue dependency that may not be available on shared hosting. The immutable send-attempt model is future-compatible with a queue/worker if deployment requirements later change.

## Send state

`OutboundSendAttempt` records:

- exact tenant/company/draft/contact references;
- the exact approved review revision that was sent;
- the ContactEmail record used as recipient;
- provider key;
- idempotency key;
- status (`PENDING`, `SENT`, `FAILED`);
- provider message identifier when available;
- safe error category;
- actor and timestamps.

The email address and message content are not duplicated into the attempt table. The approved review event remains the immutable content source of truth.

Provider failures are returned as persisted `FAILED` attempts rather than being converted into fake delivery success. Error categories are normalized; provider exception text and credentials are not persisted.

## Suppression / opt-out foundation

Tenant suppression entries are exact normalized email addresses and can be sourced from:

- `MANUAL`
- `OPT_OUT`
- `BOUNCE`
- `COMPLAINT`

The service checks suppression before any provider call. This increment intentionally does not expose an unsuppress endpoint; opt-outs should not be casually reversed.

## API

- `POST /api/v1/organizations/{org_id}/companies/{relationship_id}/campaign-drafts/{draft_id}/send`
- `GET /api/v1/organizations/{org_id}/companies/{relationship_id}/campaign-drafts/{draft_id}/send-attempts`
- `POST /api/v1/organizations/{org_id}/outbound/suppressions`
- `GET /api/v1/organizations/{org_id}/outbound/suppressions`
- `GET /api/v1/organizations/{org_id}/outbound/providers/status`

## Audit and privacy

Send audits contain only safe identifiers, provider, result status/error category, and contact/company references. They do not include recipient email, SMTP credentials, message subject/body, raw provider responses, or evidence bodies.

## Deferred

The following are intentionally deferred:

- background queues and retry workers;
- scheduled sends;
- bulk campaigns and sequences;
- automatic follow-ups;
- inbound reply ingestion;
- delivery/open/click webhooks;
- bounce/complaint webhook ingestion;
- autonomous AI outreach.
