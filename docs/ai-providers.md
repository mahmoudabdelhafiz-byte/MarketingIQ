# AI provider selection

MarketingIQ's AI features can run on **OpenAI**, **Anthropic Claude** or **DeepSeek**. The choice
is configuration only; no code or database change is needed to switch.

| Feature | What the AI does | Without AI |
| --- | --- | --- |
| Grounded campaign drafting (`CAMPAIGN`) | Writes the draft email from approved facts | Deterministic draft (free) |
| Company discovery (`DISCOVERY`) | Searches the web for companies matching an ICP | Not available |

## Choosing a provider

```text
AI_PROVIDER=openai            # openai | anthropic | deepseek — applies to every AI feature
CAMPAIGN_AI_PROVIDER=         # optional override for campaign drafting
DISCOVERY_AI_PROVIDER=        # optional override for company discovery
```

`AI_PROVIDER` defaults to `openai`, so a deployment that only sets `OPENAI_API_KEY` keeps working
exactly as before. Per-feature overrides let you mix providers, for example drafting with DeepSeek
and discovery with Claude.

Each provider reads its own key:

| Provider | Key | Default campaign model | Default discovery model |
| --- | --- | --- | --- |
| `openai` | `OPENAI_API_KEY` | `gpt-5.6-luna` | `gpt-5.5` |
| `anthropic` | `ANTHROPIC_API_KEY` | `claude-sonnet-5-5` | `claude-sonnet-5-5` |
| `deepseek` | `DEEPSEEK_API_KEY` | `deepseek-v4-flash` | `deepseek-v4-flash` |

`CAMPAIGN_AI_MODEL` and `DISCOVERY_AI_MODEL` override the model. Leave them blank to use the
provider's default. Model names change over time; confirm them in your provider console.

**Switching providers:** if `CAMPAIGN_AI_MODEL` is still set to a model from the previous vendor
(for example `gpt-5.6-luna` after switching to `anthropic`), the feature is disabled with an
explicit `configuration_error` instead of sending a bad request. Campaign drafting then falls back
to deterministic drafts. Clear or update the model variable when you switch.

`DEEPSEEK_BASE_URL` (default `https://api.deepseek.com`, HTTPS only) is available for a regional
endpoint or gateway.

## How each provider does discovery

Every adapter must report which URLs its search step actually retrieved; a candidate is kept only
if it cites one of them (see [Company discovery](company-discovery.md)).

- **OpenAI:** Responses API with the built-in `web_search` tool and
  `include: ["web_search_call.action.sources"]`. Consulted sources, opened pages and `url_citation`
  annotations count as retrieved. `max_tool_calls` is set from `DISCOVERY_MAX_SEARCHES`.
- **Claude:** Messages API with the server-side `web_search` tool (`max_uses` from
  `DISCOVERY_MAX_SEARCHES`). Search results and citations count as retrieved; `pause_turn` is
  resumed up to three times.
- **DeepSeek:** chat has no built-in search tool, so MarketingIQ runs the loop itself:
  1. the chat model plans up to `DISCOVERY_MAX_SEARCHES` queries (JSON mode),
  2. each query is sent to the DeepSeek Search API (`/v1/tools/search`, 10 results),
  3. the chat model chooses companies using only those results; their URLs are the retrieved set.

  A single failed query is skipped. Authentication, balance (`402`) and rate-limit errors stop the
  run. If no search succeeds, the run fails with `SEARCH_UNAVAILABLE`.

All search content is passed to the model as untrusted data, and the system prompt tells the model
never to follow instructions found in it.

## Cost notes

Each discovery run records the number of searches and the input/output tokens. Providers bill web
search separately from tokens and their prices change. Check each provider's current pricing page
before raising `DISCOVERY_MAX_SEARCHES` or `DISCOVERY_DAILY_RUN_LIMIT`.

## Status and preflight

- `GET .../campaign-ai/status` and `GET .../discovery/status` show the active provider, model,
  whether it is configured, and any `configuration_error`. Keys are never returned.
- `python -m marketingiq.jobs.validate_deployment` rejects an unknown provider, a model from the
  wrong vendor, a missing key for an explicitly selected provider, and a non-HTTPS
  `DEEPSEEK_BASE_URL`. It reports `campaign_ai` and `company_discovery` integration status.

## Errors

Provider failures are stored as safe categories only: `NOT_CONFIGURED`, `NETWORK_ERROR`,
`RATE_LIMITED`, `AUTHENTICATION_ERROR`, `INSUFFICIENT_BALANCE`, `PROVIDER_UNAVAILABLE`,
`PROVIDER_REJECTED_REQUEST`, plus `INVALID_PROVIDER_RESPONSE` (campaigns) or
`MALFORMED_RESPONSE`, `INCOMPLETE_RESPONSE` and `SEARCH_UNAVAILABLE` (discovery). Provider error
text, prompts and raw responses are not persisted.
