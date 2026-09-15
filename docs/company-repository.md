# Company repository, facts, evidence, and CSV import

## Shared identity and tenant relationship policy

A normalized domain is the sole company identity key in Sprint 1. Normalization trims and
lowercases input, accepts only optional HTTP(S) schemes, strips `www.`, and rejects credentials,
ports, paths, queries, fragments, IP addresses, single-label hosts, and malformed labels. The
original domain input remains in `CompanyIdentifier.value`; its normalized form is unique.

`Company` is global. The first attachment may initialize shared name, website, country, industry,
employee bounds, and description. Later manual attachments never alter an existing global record.
CSV import may fill a null shared field but never replaces a non-null one. There is no name-only or
fuzzy merge: a row without a valid domain is rejected. `OrganizationCompany.lifecycle_status` and
`private_notes` are private to that tenant and every query includes the active organization.

## Facts and provenance

Facts are append-only observations. Two observations of the same key remain separate even when
their values conflict. Tenant endpoints return global observations (`organization_id = null`) and
the active tenant's observations, never another tenant's. Tenant endpoints only create records
with the active organization ID; publishing a global fact is deferred.

Classification is one of `PUBLIC_EVIDENCE`, `THIRD_PARTY_LICENSED`, `CUSTOMER_PROVIDED`, or
`MARKETINGIQ_DERIVED`; redistribution is independently `UNKNOWN`, `ALLOWED`, `RESTRICTED`, or
`INTERNAL_ONLY`. Omitted redistribution defaults to `UNKNOWN`. A third-party licensed observation
cannot be submitted as `ALLOWED` through this API. Public and third-party facts require evidence
with a known `DataSource`; customer-provided/manual facts may omit evidence. Reference URLs, when
present, must be absolute HTTP(S) URLs without embedded credentials. No URL is fetched.

Only idempotently reused `MANUAL` and `CSV` sources can be created in this Sprint. External provider
connections are not implemented.

## CSV format and safety

The UTF-8 CSV header must contain `company_name` and `domain`. Supported optional columns are:

- `country_code`, `industry`, `employee_min`, `employee_max`, and `website_url` (shared fields);
- `lifecycle_status` and `private_notes` (tenant-private fields).

Deterministic aliases are `company` or `name` for `company_name`, `website` for `website_url`,
`country` for `country_code`, and `employees_min`/`employees_max` for employee bounds. Alias
collisions and unknown columns are rejected. Limits are 1,000,000 encoded bytes and 1,000 data
rows. Cells are strings/data only: formulas are neither interpreted nor executed.

Preview validates every row without a flush or mutation and reports counts plus row actions:
`CREATE_GLOBAL_AND_ATTACH`, `REUSE_GLOBAL_AND_ATTACH`, `ALREADY_ATTACHED`, or `REJECT`. Duplicate
normalized domains in one file are attached once; later rows are reported as already attached and
cannot overwrite values. Commit rejects the entire input if any row is invalid, then synchronously
persists valid modest imports. Audit metadata contains IDs and counts, never CSV contents or notes.

Example (fictional data only):

```csv
company_name,domain,country_code,industry,employee_min,employee_max,lifecycle_status
ABC Logistics,abc.com,SA,Logistics,200,500,NEW
XYZ Technology,xyztech.ae,AE,Technology,50,200,NEW
```
