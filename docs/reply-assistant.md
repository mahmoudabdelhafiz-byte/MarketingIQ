# Reply assistant and meeting booking

When a prospect answers an email (or a follow-up, or one of our answers), the mailbox scanner
(`python -m marketingiq.jobs.run_mailbox_engagement`, IMAP settings) matches it to the
conversation and the **Replies** page shows it with:

- **Intent**: wants a meeting, interested, question, not now, not interested, unsubscribe, out of
  office or other. Set by English/Arabic rules on arrival, by AI after *Suggest with AI*, or by you.
- **Two meeting times** on different working days (send window time zone and days, at 10:00 and
  14:00), plus the product's **booking link** if set.
- **A suggested answer** in the language the prospect wrote in (English or Arabic templates; AI for
  any language).

Nothing is sent automatically. You edit the answer and press **Send answer**; it goes in the same
email thread. Then record the outcome:

| Button | Effect |
|---|---|
| Meeting booked | POSITIVE_REPLY engagement event; pipeline opportunity moves to MEETING |
| Not interested | NEGATIVE_REPLY event; opportunity moves to LOST |
| Unsubscribe / Send & unsubscribe | OPT_OUT event; address added to the suppression list |
| Dismiss | Removed from the queue, nothing recorded |

Any reply also stops pending follow-ups and moves the opportunity to RESPONDED, as before.

## What the AI may say

*Suggest with AI* uses the campaign AI provider (`AI_PROVIDER` / `CAMPAIGN_AI_PROVIDER`). It answers
only from the product's **value proposition** and **knowledge base** (Products → Edit → *Knowledge
base for replies*). If the answer is not there it says it will confirm and come back. Answers that
contain a link or a price not found in the knowledge base (or the booking link), or a guarantee,
are rejected and replaced by the template.

## Privacy

To answer replies, MarketingIQ now stores the new part of each matched reply (quoted history is
cut off, max 8,000 characters) in `inbound_replies`. Engagement events and audit logs still never
contain reply text. Set `REPLY_CAPTURE_ENABLED=false` to keep the previous behaviour (events only,
no reply text, empty Replies page).

## API

`GET /organizations/{org}/replies?status=NEW`, and for one reply `POST …/replies/{id}/suggest`,
`/intent`, `/send` (`subject`, `body`, `then_unsubscribe`), `/meeting-booked`, `/not-interested`,
`/unsubscribe`, `/dismiss`.
