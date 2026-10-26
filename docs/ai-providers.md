# AI provider selection

MarketingIQ's AI features can run on **OpenAI**, **Anthropic Claude** or **DeepSeek**. The choice
is configuration only; no code or database change is needed to switch.

| Feature | What the AI does | Without AI |
| --- | --- | --- |
| Grounded campaign drafting (`CAMPAIGN`) | Writes the draft email from approved facts | Deterministic draft (free) |
| AI web-search discovery source (`DISCOVERY`) | Researches the web for companies matching the filters | Hunter Discover only |
| Decision-maker role checks and page extraction (`DECISION_MAKER`) | Maps job titles to buyer personas; reads team pages | Rule-based matching |

## Choosing a provider

```text
AI_PROVIDER=openai            # openai | anthropic | deepseek — applies to every AI feature
CAMPAIGN_AI_PROVIDER=         # optional override for campaign drafting
DISCOVERY_AI_PROVIDER=        # optional override for the AI discovery source
DECISION_MAKER_AI_PROVIDER=   # optional; defaults to the campaign provider
```

`AI_PROVIDER` defaults to `openai`, so a deployment that only sets `OPENAI_API_KEY` keeps working
exactly as before. Per-feature overrides let you mix providers, for example drafting with DeepSeek
and discovery with Claude.

Each provider reads its own key:

| Provider | Key | Campaign model | Discovery model | Decision-maker model |
| --- | --- | --- | --- | --- |
| `openai` | `OPENAI_API_KEY` | `gpt-5.6-luna` | `gpt-5.5` | `gpt-5.6-luna` |
| `anthropic` | `ANTHROPIC_API_KEY` | `claude-sonnet-5-5` | `claude-sonnet-5-5` | `claude-haiku-5-5` |
| `deepseek` | `DEEPSEEK_API_KEY` | `deepseek-v4-flash` | `deepseek-v4-flash` | `deepseek-v4-flash` |

`CAMPAIGN_AI_MODEL`, `DISCOVERY_AI_MODEL` and `ROLE_RESOLVER_MODEL` override the defaults. The
decision-maker feature also inherits `CAMPAIGN_AI_MODEL` when it names a model of the same vendor.
Leave them blank to use the provider's default. Model names change over time; confirm them in your
provider console.

The decision-maker JSON schema is enforced natively on OpenAI (structured outputs); Claude and
DeepSeek are instructed to follow it, and the existing validation still rejects anything that does
not match and falls back to rules.

**Switching providers:** if `CAMPAIGN_AI_MODEL` is still set to a model from the previous vendor
(for example `gpt-5.6-luna` after switching to `anthropic`), the feature is disabled with an
explicit `configuration_error` instead of sending a bad request. Campaign drafting then falls back
to deterministic drafts. Clear or update the model variable when you switch.

`DEEPSEEK_BASE_URL` (default `https://api.deepseek.com`, HTTPS only) is available for a regional
endpoint or gateway.

## How each provider does discovery

AI web search is a second discovery **source** next to Hunter Discover (see
[Company discovery](company-discovery.md)). Every adapter reports which URLs its search step
actually retrieved; a suggested company is kept only if it cites one of them.

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

Every AI discovery search (preview or scheduled rule) writes an audit entry with the provider,
model, number of web searches and input/output tokens, and counts toward
`DISCOVERY_AI_DAILY_LIMIT` (default 10 per organization per UTC day). Providers bill web search
separately from tokens and their prices change; check current pricing before raising
`DISCOVERY_MAX_SEARCHES`, `DISCOVERY_AI_MAX_RESULTS` or the daily limit.

## Status and preflight

- `GET .../campaign-ai/status` and `GET .../company-discovery/sources` show the active provider,
  model, whether it is configured, any `configuration_error` and (for AI search) today's usage.
  Keys are never returned.
- `python -m marketingiq.jobs.validate_deployment` rejects an unknown provider, a model from the
  wrong vendor, a missing key for an explicitly selected provider, and a non-HTTPS
  `DEEPSEEK_BASE_URL`. It reports `campaign_ai`, `ai_company_discovery` and `decision_maker_ai`.

## Errors

Provider failures are stored as safe categories only: `NOT_CONFIGURED`, `NETWORK_ERROR`,
`RATE_LIMITED`, `AUTHENTICATION_ERROR`, `INSUFFICIENT_BALANCE`, `PROVIDER_UNAVAILABLE`,
`PROVIDER_REJECTED_REQUEST`, plus `INVALID_PROVIDER_RESPONSE` (campaigns, decision makers) or
`MALFORMED_RESPONSE`, `INCOMPLETE_RESPONSE`, `SEARCH_UNAVAILABLE` and `DAILY_LIMIT` (discovery). Provider error
text, prompts and raw responses are not persisted.
