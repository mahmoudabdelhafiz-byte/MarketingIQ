# Current best company facts

`CompanyFact` is an immutable, append-only observation. The intelligence projection is a
tenant-scoped, computed view; it never updates or deletes an observation and is not product-fit or
lead scoring.

## Deterministic selection policy

An active human review wins first. Otherwise observations are ordered by classification
(`CUSTOMER_PROVIDED`, `PUBLIC_EVIDENCE`, `THIRD_PARTY_LICENSED`, then
`MARKETINGIQ_DERIVED`), confidence, latest evidence verification time, observation time, and fact
ID as a stable final tie-breaker. This deliberately means that newest does not always win. Missing
or unknown values are retained as unknown values; in particular, `active_hiring` is never inferred
to be false. Every projection includes a selection reason code.

An Organization Admin can approve the projected observation, select another allowed observation,
append a tenant-private customer correction, mark a conflict resolved by selecting an observation,
or revoke the active review. A manual correction has `CUSTOMER_PROVIDED` classification and
`INTERNAL_ONLY` redistribution. Reviews and revocations are audit logged. Revoking a review does
not erase it; it timestamps it and restores automatic projection.

## Conflict and freshness

Values are compared using deterministic JSON normalization. Disagreement among observations with
confidence of at least 60 is material; disagreement where all credible observations have confidence
of at least 80 needs review. Lower-quality disagreement is not allowed to displace a credible value.
Human resolution suppresses the current conflict marker without altering the disagreeing history.

Freshness is configured in days by fact key. Defaults are 30 for `active_hiring`, 180 for
`employee_range`, 365 for headquarters/locations/descriptions and 730 for industry/country.
The final quarter of a freshness interval is `AGING`; values beyond it are `STALE`. Facts without
an observation time are `UNKNOWN`. Stale facts remain visible and become review-required.

## Provenance, tenancy, and redistribution

Read Only and Marketing User memberships may read projections and permitted history. Only an
Organization Admin has the review permission. Queries require the tenant relationship and include
only global observations plus observations owned by that tenant.

The selected observation's classification and redistribution status are copied unchanged. Selection
or approval cannot upgrade legal rights. Safe evidence output contains a provider display name,
retrieval and verification timestamps, and exposes a URL only for non-licensed observations whose
redistribution status is `ALLOWED`. Raw provider payloads, credentials, restricted reference text,
and personal data are never part of intelligence output.
