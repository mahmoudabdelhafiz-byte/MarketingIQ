# Follow-ups

A follow-up is a short email sent **in the same thread** as an approved first email when the
recipient has not replied. Each sent email can have at most two (step 1 and step 2), by default
3 and 7 days after the original.

## Turning them on

- In the **Send email** dialog, tick *Follow up if there is no reply* and choose:
  - **after I approve each one** — when due, the follow-up moves to *Waiting for your approval* on
    the **Follow-ups** page, where you can send, edit, skip it or stop the whole sequence;
  - **automatically** — sent by the `run_follow_ups` job inside the send window.
- For an email that was already sent, open the draft and use **Schedule 2 follow-ups**.
- API: `POST /organizations/{org}/companies/{company}/campaign-drafts/{draft}/send-attempts/{attempt}/follow-ups`
  with `{"auto_send": false, "delays_days": [3, 7]}`, or `follow_ups: true` on the send request.

## When a follow-up is stopped

Before every send the sequence is checked again and is skipped (with a recorded reason) when:

- any reply, bounce, unsubscribe or complaint was recorded on the original or an earlier
  follow-up (`CONVERSATION_*`);
- the address is suppressed, no longer verified, or the contact was excluded or removed;
- you skip it or stop the sequence.

Step 2 never goes out before step 1. Replies to a follow-up are matched back to the original
email by the mailbox scanner, so they stop the sequence too.

## Send window and limits

| Variable | Default | Meaning |
|---|---|---|
| `FOLLOW_UP_TIMEZONE` | `Asia/Riyadh` | Time zone of the window |
| `FOLLOW_UP_SEND_HOURS` | `9-17` | Hours automatic follow-ups may be sent |
| `FOLLOW_UP_SEND_DAYS` | `sun,mon,tue,wed,thu` | Days automatic follow-ups may be sent |
| `FOLLOW_UP_DAILY_LIMIT` | `50` | Automatic follow-ups per organization per day |
| `FOLLOW_UP_BATCH_SIZE` | `50` | Items processed per job run |

**Send now** on the Follow-ups page sends immediately and ignores the window.

## Scheduled job

Add the cron line from [cPanel Passenger deployment](cpanel-passenger.md):
`python -m marketingiq.jobs.run_follow_ups` every 15 minutes. Without it, follow-ups are never
moved to approval or sent automatically (Send now still works).
