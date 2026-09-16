# Guarded Automation Runner

The guarded runner advances MarketingIQ through the deterministic pre-outreach workflow while preserving the existing safety boundaries.

## Scope

The runner may advance public research, fit assessment, lead qualification, and contact discovery. It evaluates the current plan after every step and executes each step at most once per run.

## Gates

- Provider-credit spending remains disabled unless the caller explicitly sets `allow_provider_credits=true`.
- Campaign generation is not executed by the runner.
- Campaign approval is not executed by the runner.
- Outbound sending is not executed by the runner.
- A run stops when it reaches the human campaign gate, a provider-credit gate, no further ready step, or its bounded step limit.

## API

`POST /api/v1/organizations/{org_id}/companies/{relationship_id}/automation/run?product_id={product_id}`

Optional request fields:

- `allow_provider_credits` (default `false`)
- `contact_provider` (default `HUNTER`)
- `max_contacts` (1-50, default 10)
- `max_steps` (1-4, default 4)

The response includes executed steps, the stop reason, and the final orchestration plan.

The runner is synchronous and intentionally does not require a background worker or scheduler, which keeps the current deployment compatible with normal shared-hosting constraints.
