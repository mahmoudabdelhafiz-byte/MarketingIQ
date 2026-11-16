# Importing your own contact lists

**Companies → Import contacts** adds people from your own lists (events, partners, existing
customers, an export from another tool) to MarketingIQ. It is an organization-admin action, like the
company CSV import.

## File

CSV (UTF-8; comma, semicolon or tab), up to 2,000 rows and 2 MB. Column names are matched loosely:

| Meaning | Accepted headers |
|---|---|
| Company | company, company_name, organization, account |
| Website | website, domain, company_domain, company_website (a path like `/about` is ignored) |
| Name | name / full_name, or first_name + last_name |
| Title | title, job_title, position, role |
| Email | email, email_address, work_email, business_email |
| LinkedIn | linkedin, linkedin_url, linkedin_profile |

Each row needs a name or an email, and a company website or a business email (the company is
taken from the email domain). Personal addresses (Gmail, Outlook, Yahoo…) are rejected. Preview
shows what will happen before anything is written.

## What happens

1. Each company is matched by domain or added to your companies.
2. Each person is stored once per organization (by email, or by company + name). Addresses on the
   suppression list are kept out.
3. When the company is qualified for a product, the next decision-maker run (Advance companies or
   Find decision makers) adds the people from your list, free, alongside the website and Hunter
   results. A person on both is merged into one. Pending imported people make the
   decision-maker step run again even after an earlier search was blocked.
4. They go through the same checks as everyone else: persona role match, evidence, and email
   verification before drafting. Imported emails are treated as unverified; verify them with
   Hunter (or Find email) before sending.

**Trusted list**: tick it for lists you know are current (for example existing customers). People
are then pre-approved (the same as pressing Approve), so a single source is enough; their emails
still need verifying.

Imported people are classified as `CUSTOMER_PROVIDED` data.

## API

`POST /organizations/{org}/contact-imports/preview` `{content}`,
`POST /organizations/{org}/contact-imports` `{content, list_name, trusted}`,
`GET /organizations/{org}/contact-imports` (lists with pending / attached / suppressed counts).
