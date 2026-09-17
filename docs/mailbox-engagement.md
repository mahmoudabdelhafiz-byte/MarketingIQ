# Shared-mailbox engagement ingestion

MarketingIQ can ingest real replies and hard delivery failures from the same mailbox used for SMTP outreach, without requiring a paid ESP webhook integration.

This increment is intentionally conservative:

- IMAP is read-only and does not change message read/unread state.
- Human replies are matched only through standard `In-Reply-To` / `References` message IDs that correspond to a previously `SENT` SMTP attempt.
- Delivery-status notifications are treated as `BOUNCED` only when a `message/delivery-status` part explicitly reports `Action: failed` or a `5.x.x` status and the original MarketingIQ message ID can be recovered.
- Automatic responses such as `Auto-Submitted: auto-replied` are ignored rather than counted as human replies.
- Reply bodies, sender/recipient addresses, complete mailbox messages, credentials, and raw DSN payloads are never persisted in engagement or audit records.
- No sentiment is inferred. A mailbox reply becomes the neutral `REPLIED` outcome; positive/negative classification remains a human action.

The ingestor reuses the existing provider engagement service, so events remain append-only, system-attributed, tenant-scoped, and idempotent. Bounces automatically create the existing tenant suppression entry and never auto-unsuppress a recipient.

## Configuration

Set these only in the deployment environment or secret manager:

```text
IMAP_HOST=mail.example.com
IMAP_PORT=993
IMAP_USERNAME=marketing@example.com
IMAP_PASSWORD=replace-me
IMAP_MAILBOX=INBOX
IMAP_USE_SSL=true
IMAP_STARTTLS=false
MAILBOX_ENGAGEMENT_BATCH_SIZE=100
```

`IMAP_PASSWORD` must never be committed. The mailbox identity used for event idempotency is a SHA-256-derived fingerprint; the username is not persisted in engagement records.

## Shared-hosting cron

Run the bounded scanner from cron:

```bash
python -m marketingiq.jobs.run_mailbox_engagement
```

Each run scans only the latest configured batch window and safely reprocesses messages because the mailbox UID plus mailbox fingerprint produces an idempotent provider event ID. The job prints only aggregate counts: processed, matched, replies, bounces, and skipped.

A skipped message is expected when it is unrelated mail, lacks a trustworthy thread reference, is an automatic response, or cannot be matched unambiguously to exactly one prior SMTP send attempt.

## Boundaries

This does not claim generic SMTP provides delivery telemetry. It adds two outcomes that can be obtained from a normal shared mailbox: standards-based replies and explicit DSN failures. Provider-native delivery, complaint, open/click, and richer bounce events still belong to ESP-specific adapters or signed provider webhooks.
