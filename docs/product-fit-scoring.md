# Deterministic Product / ICP fit scoring

Fit assessments are immutable, tenant-owned historical records. Each run records the Product, the
exact versioned ICP, workflow version, current-best fact identifiers, and a structured explanation.
Re-evaluation creates a new row; it never edits an earlier result.

## Criteria and weights

ICP criteria use normalized `kind` and `value` fields plus an integer `weight` (1–100) and a
`required` flag. Weight is the entire influence of a criterion, so configurations can represent
required/strong, preferred/medium, and bonus/low importance without product-specific code. Supported
kinds are industry, country, employee_min, employee_max, company_size, active_hiring,
business_service, location, keyword, and presence_signal. ICP employee bounds are also evaluated at
the default weight of 3.

`MATCH` earns the full weight, `PARTIAL_MATCH` one half, and `NO_MATCH` zero. The fit score is
`100 * matched weight / known evaluated weight`, rounded to an integer. Grades are A (80–100), B
(65–79), C (50–64), and D (0–49). No known evidence produces `UNKNOWN`. A definitive required
mismatch caps the score at 39 and the grade at D.

## Unknowns, coverage, and quality

`UNKNOWN` is not a failed criterion and is excluded from the score denominator. Evidence coverage is
reported separately as `100 * known evaluated weight / total configured weight`; confidence is HIGH
at 80%+, MEDIUM at 50–79%, and LOW below 50%. Thus a high score at low coverage remains visibly weak.
A required unknown produces `NEEDS_MORE_RESEARCH`, not rejection.

Status is `CONFLICTED` when a selected projection has material conflict, `NEEDS_MORE_RESEARCH` for a
required unknown, `STALE` for stale selected evidence, `PARTIAL` for other gaps, and `COMPLETE` when
all evidence is known and current (in that precedence order).

## Explainability and redistribution

The structured explanation exposes every expected and actual normalized value, result, safe reason,
weight, current-best selected fact ID, confidence and quality; it also groups positive/negative
factors, research gaps and stale/conflicted facts. Snapshots intentionally omit evidence text,
provider payloads, and restricted URLs. Assessment output is classified `MARKETINGIQ_DERIVED`; that
does not change the redistribution rights of underlying facts.
