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

## Durable mailbox cursor

The shared-hosting cron path stores one operational checkpoint for the configured mailbox. The checkpoint contains only:

- a SHA-256-derived mailbox identity fingerprint;
- the IMAP `UIDVALIDITY` value;
- the last successfully fetched UID;
- checkpoint timestamps.

It does not store the IMAP host, username, password, sender/recipient addresses, subject, message body, or raw mailbox content.

On the first run, MarketingIQ intentionally bootstraps from only the latest configured batch window rather than importing the mailbox's full historical contents. After that, runs process the oldest unseen UIDs first, so a backlog larger than one batch is drained across successive cron executions without skipping the middle of the backlog.

If the IMAP server changes `UIDVALIDITY`, the previous UID cursor is no longer trustworthy. MarketingIQ resets the cursor and safely bootstraps from the latest batch of the new mailbox generation. A failed IMAP fetch stops cursor advancement at the last successfully fetched UID so the failed message can be retried on the next run.

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

`IMAP_PASSWORD` must never be committed. The mailbox identity used for event idempotency and cursor lookup is a SHA-256-derived fingerprint; the username is not persisted in engagement or checkpoint records.

## Shared-hosting cron

Run the bounded scanner from cron:

```bash
python -m marketingiq.jobs.run_mailbox_engagement
```

Each run first takes a zero-wait MySQL advisory lock scoped to the configured MarketingIQ database. If the same mailbox job is already running, the overlapping invocation exits successfully with `status=skipped` and `reason=already_running`, avoiding concurrent cursor scans. Otherwise it fetches at most the configured batch size. On an established checkpoint it selects only UIDs newer than the durable cursor, oldest first. The job prints only aggregate counts: processed, matched, replies, bounces, and skipped.

A skipped message is expected when it is unrelated mail, lacks a trustworthy thread reference, is an automatic response, or cannot be matched unambiguously to exactly one prior SMTP send attempt. Skipped messages are still considered consumed mailbox input; they do not expose or persist their private content.

## Boundaries

This does not claim generic SMTP provides delivery telemetry. It adds two outcomes that can be obtained from a normal shared mailbox: standards-based replies and explicit DSN failures. Provider-native delivery, complaint, open/click, and richer bounce events still belong to ESP-specific adapters or signed provider webhooks.
