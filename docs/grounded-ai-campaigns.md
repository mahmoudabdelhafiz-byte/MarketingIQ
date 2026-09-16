# Grounded AI campaign drafting

MarketingIQ keeps deterministic campaign drafting as the default zero-cost path and adds an optional AI-assisted endpoint for stronger copy.

## Safety model

The AI adapter receives only:
- company name;
- selected buyer role and recipient first name;
- product name and configured value proposition;
- the requested call to action; and
- fit facts that are both `PUBLIC_EVIDENCE` and `ALLOWED` for redistribution.

It never receives raw provider responses, provider contact references, email addresses, internal notes, or restricted evidence. The generated message remains `DRAFT` and follows the existing human edit/approve/send gates.

AI output must declare which approved fact keys it used. Any unsupported key or blocked claim pattern fails grounding validation. A caller may opt into deterministic fallback when the provider is unavailable or validation fails.

## Cost control

Leave `OPENAI_API_KEY` empty to keep deterministic generation only. The AI endpoint defaults to deterministic fallback when AI is unavailable. The configured model is controlled by `CAMPAIGN_AI_MODEL`.

## API

- `POST /api/v1/organizations/{org_id}/companies/{relationship_id}/campaign-drafts/ai`
- `GET /api/v1/organizations/{org_id}/campaign-ai/status`

The generation request contains `qualification_id`, `contact_id`, optional `call_to_action`, and `fallback_to_deterministic`.

## Audit and privacy

`campaign_ai_generation_attempts` stores operational metadata only: provider/model, prompt version, safe input fact keys, token counts, status, error category, and request fingerprint. Prompt text and raw AI responses are not persisted.

No production deployment or migration is performed by this feature branch.