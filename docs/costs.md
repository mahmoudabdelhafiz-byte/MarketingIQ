# Costs

**Costs** in the web interface estimates what outreach costs per lead, reply and meeting for the
last 7, 30 or 90 days or 12 months.

## Spend = recorded usage × your prices

| Line | Usage recorded by MarketingIQ | Price you set (Cost settings) |
|---|---|---|
| Provider credits (Hunter) | `credits_used` of company research, people search, email find/verify (cached lookups excluded) | per credit: plan price ÷ monthly credits |
| AI input / output tokens | campaign drafts, AI company discovery, AI reply suggestions | per 1M tokens, from your AI provider's price list |
| AI web searches | searches made by AI company discovery | per search |
| Fixed costs | — | monthly subscriptions/hosting, spread pro rata over the period |

A price left empty counts as zero and the page says so. Decision-maker AI role checks do not record
tokens yet, so they are not in the AI lines.

## Results in the same period

- **Lead**: a person whose business email was verified in the period.
- **Emails sent** (and follow-ups), **replies** (conversations with any reply), **meetings** and
  **won** (opportunities that reached those stages in the period).
- **Cost per lead / reply / meeting** = total spend ÷ that count.
- **By product**: emails, replies, meetings and rates per product, to see where outreach works.
  Spend itself is not split by product.

## API

`GET /organizations/{org}/costs/report?days=30`, `GET` and `PUT /organizations/{org}/costs/settings`
(`currency`, `provider_credit_cost`, `ai_input_per_million`, `ai_output_per_million`,
`web_search_cost`, `monthly_fixed_cost`). Changing prices needs the catalog-write permission.

## Dashboard

**Reports → Dashboard** (`GET /organizations/{org}/dashboard?days=30|90|180|365`) shows, for the
period: emails sent, reply rate, meetings, open opportunities and cost per meeting (each compared
with the previous period); weekly emails, replies and meetings; the funnel; where every opportunity
stands now; reply rate by product; what prospects replied; and recent activity. Every chart can be
shown as a table. The **Today** page stays the place for what needs doing now.
