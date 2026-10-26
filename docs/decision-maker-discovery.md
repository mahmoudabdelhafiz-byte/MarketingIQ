# Decision-maker discovery

Finds the right people at a **qualified** company for a product's target personas, with stored evidence for every claim. It replaces the single "recommended buyer role" search for products that have personas; products without personas keep the original contact-discovery step unchanged.

## Personas (product configuration)

A persona is a named target group on a product: job titles, departments, seniority, and how many people to select (1–3). Edit them on **Products → (product) → Target personas**. The editor offers suggested templates (Executive leadership, IT leadership, HR leadership, Communications, Marketing). They are starting points only: the discovery engine has no built-in roles, and a template becomes an ordinary persona you can edit.

If a product has no personas saved, discovery falls back to the product's primary and secondary buyer roles, one persona per role.

API: `GET/PUT /api/v1/organizations/{org}/products/{product}/personas` (`WRITE_CATALOG` to change).

## What runs, cheapest first

1. **Company website (free).** The homepage and at most four same-domain pages linked as leadership, management, team, board or about. Fetching goes through the SSRF-safe public fetcher: HTML only, 1 MB cap, 3 redirects, public IPs only. People are read as "name followed by title" blocks. If none are found and OpenAI is configured, one AI call reads the most promising page. Every name and title it returns must appear verbatim in the page text, or it is discarded.
2. **Role resolution.** Deterministic rules expand unambiguous abbreviations (CEO, CIO, CTO, CHRO, MD, GM, VP, HR, IT and others). They match titles against each persona's aliases and flag supporting roles (assistant, coordinator), deputy roles and former roles. Titles the rules cannot settle, such as an ambiguous "CPO", are sent in **one** batched AI call. The call uses a strict JSON schema and accepts only supplied candidate IDs and configured persona names. Confidence is clamped and AI "HIGH" results are capped at 90. Any AI failure keeps the rule result.
3. **Hunter (credits, opt-in).** This runs only when *Allow provider credits* is ticked and the user has `SPEND_PROVIDER_CREDITS`. It makes one Domain Search with `type=personal` and the union of persona departments and seniorities mapped to Hunter's values (for example IT/Technology/Digital → `it`, HR/People → `hr`, C-level → `executive`, Director/Head → `senior`). Results are cached for `CONTACT_SEARCH_FRESHNESS_HOURS`.
4. **Email Finder and Verifier (credits, opt-in).** These run only for the top-ranked people per persona, with a hard cap of 12 operations per run. A Finder lookup that returns nothing is not repeated within `EMAIL_FIND_FRESHNESS_HOURS`. A verification status reported by Hunter is accepted only if it is newer than `EMAIL_VERIFY_FRESHNESS_HOURS`; otherwise the Verifier runs.

Public-web, OpenAI, Hunter-search, finder and verifier operations each get their own `ProviderUsage` row: `LEADERSHIP_SCAN`, `PEOPLE_EXTRACTION`/`ROLE_RESOLUTION`, `DECISION_MAKER_SEARCH`, `EMAIL_FIND` and `EMAIL_VERIFY`. Each run's audit entry (`decision_makers.discovered`) holds the per-category counts.

Search-engine lookups are **not** performed, because no approved search API is configured. This is reported as `SEARCH_ENGINE_LOOKUP_NOT_CONFIGURED` in the run notes.

## LinkedIn

LinkedIn is never requested, crawled, scraped or logged into. A LinkedIn profile URL is stored only when the company's own page links it next to the person, or when Hunter returns it. It is kept as a reference (`PUBLIC_PROFILE_URL` evidence, "not fetched") and does not count as corroborating evidence.

## Evidence

Every claim is stored in `contact_evidence` with these fields:
- source type: `COMPANY_WEBSITE`, `PROVIDER_RECORD`, `PROVIDER_SOURCE_PAGE`, `PUBLIC_PROFILE_URL` or `EMAIL_VERIFICATION`
- source URL
- snippet
- claimed name, title and company
- retrieved-at timestamp
- the provider's last-seen date and still-on-page flag
- confidence

If a later scan of the same company page no longer lists a person, that evidence is marked `still_on_page = false`.

People are de-duplicated within the company and product in this order: email, provider reference, normalised LinkedIn URL, then exact normalised full name. There is no fuzzy matching. New evidence is merged into the existing record. The company website is the authority for the current title.

## Employment state

| State | Rule |
|---|---|
| `CURRENT_CONFIRMED` | Listed on the company's own website, checked within 180 days |
| `CURRENT_LIKELY` | A provider source still lists them and was seen within a year, or a recent verified mailbox |
| `CURRENT_UNCERTAIN` | No recent confirmation; delisted from the company page; old website evidence |
| `FORMER` | A title says former/ex/retired, or the mailbox is invalid and no source still lists them |
| `CONFLICTING` | The company website and the provider give clearly different roles |

`FORMER` and `CONFLICTING` people are never selected automatically. Campaign drafting and sending also refuse them unless a user approves them.

## Scoring (0–100, shown in the UI)

The score adds four parts:
- role: HIGH 40, MEDIUM 25, LOW 10
- employment: confirmed 30, likely 20, uncertain 8
- independent sources: 1 → 5, 2 → 10, 3+ → 15
- email: verified 15, unverified 7

Grades:
- `HIGH` needs a HIGH role match and a score of 70 or more.
- `MEDIUM` needs a HIGH or MEDIUM role match and a score of 45 or more.
- `INSUFFICIENT_EVIDENCE` applies to conflicting sources, or to an uncertain employment state with fewer than two independent sources.
- `LOW` covers everything else.

The rule applied is stored with the components.

## Contact status and outcome

Per person, the status is one of:
- `READY_FOR_DRAFT`: relevant, sufficient evidence and a **verified** email
- `EMAIL_UNVERIFIED`
- `CONTACT_FOUND_EMAIL_MISSING`
- `INSUFFICIENT_EVIDENCE`
- `NOT_RELEVANT`
- `EXCLUDED`

The best 1–3 people per persona are ranked in this order:
1. approved
2. suitability
3. employment state
4. role confidence
5. status

The company outcome is one of `READY_FOR_DRAFT`, `EMAIL_UNVERIFIED`, `CONTACT_FOUND_EMAIL_MISSING`, `INSUFFICIENT_EVIDENCE`, `NO_RELEVANT_DECISION_MAKER` or `NO_CONTACTS_FOUND`.

Emails come only from a provider or from a user. MarketingIQ never builds an address from a name pattern. A missing email stays missing.

## Workflow integration

For products with personas, the guided workflow's *discover contacts* step behaves as follows:
- It stays **blocked until the lead is qualified** (`HIGH_PRIORITY` or `QUALIFIED` with a current fit assessment).
- It runs the public-web phase even without provider credits.
- It counts as done only when at least one person is `READY_FOR_DRAFT`.
- It does not search again within `CONTACT_SEARCH_FRESHNESS_HOURS`. The exception is a run that allows credits after a public-only run, which is allowed once. The UI's **Refresh** button forces a new search.

Scheduled automation (company discovery rules and weekly company schedules) follows the same rules.

## Manual controls

The Decision makers tab has Discover, Refresh, Find email, Verify email, Approve, Exclude and Reset review. Approve and Exclude require `RUN_QUALIFICATION`. Each creates a `decision_makers.review` audit entry with the actor, note, and before/after review and contact status. Approval lets a person be used despite evidence concerns. Their email must still be verified before drafting.

API under `/api/v1/organizations/{org}/companies/{relationship}/decision-makers`:
- `GET` (summary; `?product_id=`)
- `POST /discover` with `{product_id?, allow_provider_credits, force_refresh}`
- `GET /{contact}` (detail with evidence, emails and audit trail)
- `POST /{contact}/review` with `{action: APPROVE|EXCLUDE|RESET, note?}`
- `POST /{contact}/find-email`
- `POST /{contact}/verify-email`

The AI rationale shown is a one-sentence, evidence-citing reason. Model reasoning is never requested or stored.

## Configuration

No new required settings. Existing variables are reused:

| Variable | Use |
|---|---|
| `HUNTER_API_KEY` | Hunter search, finder and verifier (credit phase) |
| `OPENAI_API_KEY` | Enables AI role resolution and page extraction; blank = rules only |
| `CAMPAIGN_AI_MODEL`, `CAMPAIGN_AI_TIMEOUT_SECONDS` | Model and timeout for the AI calls |
| `ROLE_RESOLVER_MODEL` (optional) | A cheaper model just for role checks; defaults to `CAMPAIGN_AI_MODEL` |
| `CONTACT_SEARCH_FRESHNESS_HOURS`, `EMAIL_FIND_FRESHNESS_HOURS`, `EMAIL_VERIFY_FRESHNESS_HOURS` | Cache windows that prevent repeat spend |

Database changes are in migration `20260923_21`:
- new tables `product_personas` and `contact_evidence`
- new nullable columns on `contact_candidates` and `contact_emails`

The migration is idempotent and existing contact rows remain valid.
