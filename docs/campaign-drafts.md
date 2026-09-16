# Campaign draft generation

MarketingIQ can now turn a current, actionable lead qualification and an aligned verified contact
into a tenant-private email draft. This increment prepares copy only; it does not send email,
change contact state, schedule outreach, or call an AI/provider service.

## Eligibility gate

A draft can be generated only when all of the following are true:

- the actor is an Organization Admin or Marketing User;
- the company relationship, qualification, product, and contact all belong to the same tenant;
- the qualification status is `HIGH_PRIORITY` or `QUALIFIED`;
- the qualification's fit assessment is still current;
- the contact belongs to that exact qualification and has `EXACT`, `STRONG`, or `PARTIAL`
  buyer-role alignment;
- the contact has a business email verified as `VALID`;
- the Product has a non-empty value proposition.

A failure at any gate produces an explicit conflict rather than silently generating weak or
unsupported copy.

## Grounding and privacy

`CampaignDraft` is append-only and classified as `MARKETINGIQ_DERIVED` with
`INTERNAL_ONLY` redistribution. The deterministic V1 composer uses only the tenant Product value
proposition, company name, qualification decision, and the selected contact's role context. It does
not invent company pain points, budgets, projects, intent, or timing.

The stored evidence snapshot contains IDs, qualification/fit scores, and matched criterion IDs. It
does not duplicate provider payloads, email addresses, provider contact references, or raw evidence.
Audit metadata similarly records only safe IDs, channel, angle, and workflow version. Because the
message body may contain licensed contact name/title information, drafts are tenant-private and are
not suitable for a public/commercial API.

## API

Under `/api/v1/organizations/{org_id}/companies/{relationship_id}/campaign-drafts`:

- `POST /` creates a new EMAIL draft and never mutates an earlier draft.
- `GET /` lists the tenant's historical drafts for that company relationship.
- `GET /{draft_id}` reads one tenant-scoped draft.

The request accepts `qualification_id`, `contact_id`, optional `channel` (currently only `EMAIL`),
and an optional human-supplied call to action.

## Deferred intentionally

Human approval/rejection actions, editing/versioning, sequence/campaign grouping, send scheduling,
mailbox/provider integrations, delivery/open/reply tracking, autonomous outreach, and LLM-based
copy generation are deliberately deferred. A later generator adapter can replace or augment the
deterministic composer while preserving the same evidence, tenancy, approval, and audit gates.
