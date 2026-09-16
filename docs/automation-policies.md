# Automation Policies and Cron Execution

MarketingIQ automation policies schedule only the existing guarded pre-outreach workflow:

`public research -> fit assessment -> qualification -> contact discovery`

Campaign generation, campaign approval, and outbound sending remain explicit human actions.

## Policy safeguards

- Organization Admin is required to create, update, enable, disable, or manually run due policies.
- Read-only users may inspect policy configuration and run history.
- A policy stores the user who created it as `run_as_user_id`.
- Every scheduled execution re-checks that user's current active account, tenant membership, and permissions.
- Provider credits are disabled by default. Contact discovery can spend credits only when the policy explicitly has `allow_provider_credits=true` and the run-as user still has `SPEND_PROVIDER_CREDITS`.
- Cadence is bounded to 60 minutes through 30 days.
- Each policy/company/product scope is unique.
- Each `(policy_id, scheduled_for)` execution is unique, making cron retries idempotent.
- Run audit metadata never contains contact email addresses, message bodies, or raw provider payloads.

## API

Create a policy:

```http
POST /api/v1/organizations/{org_id}/automation-policies
```

Example body:

```json
{
  "relationship_id": "<organization-company-id>",
  "product_id": "<product-id>",
  "cadence_minutes": 1440,
  "allow_provider_credits": false,
  "contact_provider": "HUNTER",
  "max_contacts": 10,
  "max_steps": 4
}
```

Useful endpoints:

- `GET /api/v1/organizations/{org_id}/automation-policies`
- `GET /api/v1/organizations/{org_id}/automation-policies/{policy_id}`
- `PATCH /api/v1/organizations/{org_id}/automation-policies/{policy_id}`
- `GET /api/v1/organizations/{org_id}/automation-policies/{policy_id}/runs`
- `POST /api/v1/organizations/{org_id}/automation-policies/run-due`

The manual `run-due` endpoint is tenant-scoped and requires Organization Admin permission.

## Shared-hosting cron

A background worker is not required. A hosting cron entry can periodically run:

```bash
python -m marketingiq.jobs.run_automation_policies
```

Required environment:

- `DATABASE_URL`
- provider environment variables already used by the configured adapters, if any policy permits provider credits

Optional:

- `AUTOMATION_CRON_BATCH_SIZE` (default `20`, maximum `100`)

The cron process scans enabled policies that are due across tenants. It does not impersonate a super-admin. Instead, each policy is executed under its stored `run_as_user_id`, and that user's current tenant membership and permissions are checked before any orchestration step is executed.

## Failure behavior

A failed scheduled run is recorded with a safe error code and the policy advances to its next cadence. This avoids a tight retry loop. The next scheduled execution can retry normally after configuration, membership, or provider problems are corrected.

No production cron job is created by this feature. Hosting configuration remains a separate deployment step.
