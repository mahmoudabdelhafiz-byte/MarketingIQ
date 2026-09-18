# Lead qualification and recommended buyer role

## Product Fit versus Lead Qualification

Product Fit answers whether the current-best company intelligence matches a versioned ICP. Lead
Qualification consumes that immutable `ProductFitAssessment` and answers whether the relationship is
actionable now. It never reads raw `CompanyFact` rows and never repeats current-best selection or fit
scoring. The distinction matters: excellent apparent fit with sparse evidence is not an equally strong
sales opportunity.

Every execution writes a new tenant-scoped `LeadQualification`. Existing rows are never updated.
Manual reevaluation therefore preserves the prior decision, even when buyer-role configuration or the
freshness of the source assessment has changed.

## Deterministic score and confidence

Workflow `deterministic-qualification-v1` uses this exact formula, rounded to the nearest integer:

```text
qualification_score = round(
    0.60 * fit_score
  + 0.20 * evidence_coverage
  + 0.10 * fit_confidence_component
  + 0.10 * freshness_component
)
```

Fit confidence maps `HIGH=100`, `MEDIUM=60`, and `LOW=30`; an unknown confidence maps to zero.
Freshness maps `CURRENT=100` and every non-current state to zero. Components, weights, and the final
score are persisted for explanation and repeatability. For example, fit 92, coverage 55, high
confidence, and current freshness scores 86. Coverage affects both the score and status, preventing
weakly evidenced fit from looking ready to act on.

Grades describe qualification, not fit: `A >= 80`, `B >= 65`, `C >= 50`, and `D < 50`. Zero evidence
coverage produces `UNKNOWN`, regardless of the numerical score.

## Status thresholds and freshness

Status rules are evaluated in this order:

1. Any non-current freshness result produces `STALE`.
2. A known `NO_MATCH` on a required criterion produces `NOT_QUALIFIED`.
3. A required unknown, coverage below 50, or a conflicted/stale/research-needed fit status produces
   `NEEDS_MORE_RESEARCH`.
4. Score at least 80, fit at least 80, and coverage at least 80 produces `HIGH_PRIORITY`.
5. Score at least 60 produces `QUALIFIED`.
6. Known poor fit below 40 with coverage at least 50 produces `NOT_QUALIFIED`.
7. Remaining cases produce `NURTURE`.

`FitAssessmentFreshnessService` is the only freshness authority. Intelligence changes, evidence-quality
changes, and superseding ICP revisions cannot silently retain a current qualification. Qualification
does not mutate or automatically rerun the fit assessment.

## Buyer-role configuration and recommendation

Products expose ordered `primary_buyer_roles` and `secondary_buyer_roles`. Only Organization Admins
may change them. Values are organizational roles—not people—and the workflow does no contact discovery.
The first configured primary role is recommended with `HIGH` confidence and reason code
`PRODUCT_PRIMARY_BUYER`; remaining primary roles followed by secondary roles are alternatives.

When no primary role is configured, the recommendation is `UNKNOWN` with `LOW` confidence, reason code
`INSUFFICIENT_ROLE_CONFIGURATION`, and a structured research gap. Configuration takes precedence over
hardcoded product or industry assumptions; this workflow deliberately has no product-name rules.

## Research gaps, UNKNOWN, and safe explanations

Gaps are deterministic objects containing `code`, `severity`, optional `criterion_id`/`fact_key`, and a
normalized reason. Current codes cover required ICP unknowns, coverage below 50, stale fit assessments,
and absent buyer-role configuration. `UNKNOWN` never means `NO_MATCH`: missing required evidence leads
to `NEEDS_MORE_RESEARCH`, while `NOT_QUALIFIED` requires a known blocker or sufficiently evidenced poor
fit.

Qualifications are classified `MARKETINGIQ_DERIVED`. Explanations contain normalized criterion IDs,
fact keys, reason codes, scores, and status only. They do not include evidence bodies, provider payloads,
credentials, or source URLs. Audit events likewise contain only IDs, score, status, and recommended
role.

## API and access

Authenticated tenant-scoped routes create, list, retrieve, and reevaluate qualifications under
`/api/v1/organizations/{org_id}/companies/{relationship_id}/qualifications`. Read Only members can list
and retrieve. Marketing Users and Organization Admins can execute and reevaluate. Every lookup binds
both organization and relationship IDs, preventing cross-tenant access.
