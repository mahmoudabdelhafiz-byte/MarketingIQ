# Guarded Automation Runner API Example

Request:

```json
{
  "allow_provider_credits": false,
  "contact_provider": "HUNTER",
  "max_contacts": 10,
  "max_steps": 4
}
```

Expected behavior:

- Run public research, fit assessment, and qualification when those steps are ready.
- Stop before contact discovery when provider-credit approval has not been granted.
- If provider credits are explicitly allowed, contact discovery may run through the existing provider adapter and usage controls.
- Always stop before campaign generation, approval, or outbound sending.

Representative stop reasons:

- `PROVIDER_CREDIT_APPROVAL_REQUIRED`
- `HUMAN_CAMPAIGN_GATE`
- `NO_READY_STEP`
- `MAX_STEPS_REACHED`
