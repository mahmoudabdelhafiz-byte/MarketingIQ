"""Find new target companies that match a product's ICP, on demand or on a schedule.

Discovery only adds companies to the tenant's list (marked ``discovered``) and can optionally
run the existing guarded pre-outreach workflow (research, fit, qualification and, if credits are
explicitly allowed, contact discovery) for each new company. It never drafts, approves or sends
email; those remain human actions.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from marketingiq.application.authorization import (
    ROLE_PERMISSIONS,
    Permission,
    require_permission,
)
from marketingiq.application.automation_policies import _as_utc, _next_run_after
from marketingiq.application.companies import CompanyService, normalize_domain
from marketingiq.application.errors import NotFoundError
from marketingiq.application.orchestration import AutomationOrchestrationService
from marketingiq.application.research import ProviderRegistry
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.discovery import (
    HEADCOUNT_BUCKETS,
    CompanyDiscoveryPolicy,
    CompanyDiscoveryProvider,
    CompanyDiscoveryRun,
    CompanyDiscoveryRunStatus,
    DiscoveredCompany,
    DiscoveryCriteria,
    DiscoveryResult,
)
from marketingiq.domain.models import (
    ICP,
    AuditLog,
    CompanyIdentifier,
    OrganizationCompany,
    OrganizationMembership,
    Product,
    User,
)
from marketingiq.domain.providers import ProviderError, ProviderNotConfigured

DISCOVERED_LIFECYCLE = "discovered"
AI_SEARCH_ACTION = "company_discovery.ai_search"
MAX_EXCLUDED_DOMAINS = 200
MIN_CADENCE_MINUTES = 60
MAX_CADENCE_MINUTES = 43200
MAX_LIST_ITEMS = 25
_COUNTRY = re.compile(r"^[A-Za-z]{2}$")
_BUCKET_BOUNDS = (
    ("1-10", 1, 10),
    ("11-50", 11, 50),
    ("51-200", 51, 200),
    ("201-500", 201, 500),
    ("501-1000", 501, 1000),
    ("1001-5000", 1001, 5000),
    ("5001-10000", 5001, 10000),
    ("10001+", 10001, None),
)


class DiscoveryDailyLimitReached(ProviderError):
    category = "DAILY_LIMIT"


def ai_daily_limit() -> int:
    try:
        return max(1, int(os.environ.get("DISCOVERY_AI_DAILY_LIMIT", "10")))
    except ValueError:
        return 10


def _provider_map(
    primary: CompanyDiscoveryProvider, extra: Mapping[str, CompanyDiscoveryProvider] | None
) -> dict[str, CompanyDiscoveryProvider]:
    providers = {primary.key: primary}
    for key, provider in (extra or {}).items():
        providers.setdefault(key, provider)
    return providers


def _costs_credits(provider: CompanyDiscoveryProvider) -> bool:
    return bool(getattr(provider, "costs_credits", False))


def _day_start(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _paid_searches_today(session: Session, organization_id: str, now: datetime) -> int:
    return int(
        session.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.organization_id == organization_id,
                AuditLog.action == AI_SEARCH_ACTION,
                AuditLog.created_at >= _day_start(now),
            )
        )
        or 0
    )


def _check_budget(
    session: Session, organization_id: str, provider: CompanyDiscoveryProvider, now: datetime
) -> None:
    if _costs_credits(provider) and _paid_searches_today(session, organization_id, now) >= (
        ai_daily_limit()
    ):
        raise DiscoveryDailyLimitReached("Daily AI discovery search limit reached")


def _start_spend(
    session: Session,
    *,
    organization_id: str,
    actor_user_id: str | None,
    entity_id: str,
    context: str,
    provider: CompanyDiscoveryProvider,
    now: datetime,
) -> AuditLog | None:
    """Count a paid search before it starts, so failed or partial searches use budget too."""

    if not _costs_credits(provider):
        return None
    if not provider.configured:
        raise ProviderNotConfigured("AI discovery source is not configured")
    entry = AuditLog(
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        action=AI_SEARCH_ACTION,
        entity_type="company_discovery",
        entity_id=entity_id,
        metadata_json={"context": context, "source": provider.key, "status": "started"},
        created_at=now,
    )
    session.add(entry)
    session.flush()
    return entry


def _finish_spend(entry: AuditLog | None, result: DiscoveryResult) -> None:
    if entry is None:
        return
    usage = {k: v for k, v in (result.usage or {}).items() if isinstance(v, int | str)}
    entry.metadata_json = {
        **(entry.metadata_json or {}),
        "status": "completed",
        "companies": len(result.companies),
        **usage,
    }


def _tenant_domains(session: Session, organization_id: str) -> tuple[str, ...]:
    rows = session.scalars(
        select(CompanyIdentifier.normalized_value)
        .join(OrganizationCompany, OrganizationCompany.company_id == CompanyIdentifier.company_id)
        .where(
            OrganizationCompany.organization_id == organization_id,
            CompanyIdentifier.kind == "DOMAIN",
        )
        .order_by(OrganizationCompany.created_at.desc())
        .limit(MAX_EXCLUDED_DOMAINS)
    )
    return tuple(rows)


def headcount_buckets(employee_min: int | None, employee_max: int | None) -> tuple[str, ...]:
    if employee_min is None and employee_max is None:
        return ()
    low = employee_min or 0
    high = employee_max
    selected = []
    for label, bucket_low, bucket_high in _BUCKET_BOUNDS:
        overlaps_low = bucket_high is None or bucket_high >= low
        overlaps_high = high is None or bucket_low <= high
        if overlaps_low and overlaps_high:
            selected.append(label)
    return tuple(selected)


def _clean_list(values: Any, *, limit: int = MAX_LIST_ITEMS, max_length: int = 100) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = values.split(",")
    cleaned: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in cleaned:
            cleaned.append(text[:max_length])
    return cleaned[:limit]


def normalize_criteria(
    *,
    query: str | None = None,
    countries: Any = None,
    industries: Any = None,
    keywords: Any = None,
    headcounts: Any = None,
) -> DiscoveryCriteria:
    country_codes = [value.upper() for value in _clean_list(countries)]
    invalid = [value for value in country_codes if not _COUNTRY.match(value)]
    if invalid:
        raise ValueError("countries must be ISO 3166-1 alpha-2 codes such as SA or AE")
    buckets = _clean_list(headcounts, limit=len(HEADCOUNT_BUCKETS))
    unknown = [value for value in buckets if value not in HEADCOUNT_BUCKETS]
    if unknown:
        raise ValueError(f"headcounts must be among: {', '.join(HEADCOUNT_BUCKETS)}")
    clean_query = (query or "").strip()[:500] or None
    criteria = DiscoveryCriteria(
        query=clean_query,
        countries=tuple(country_codes),
        industries=tuple(_clean_list(industries)),
        keywords=tuple(_clean_list(keywords)),
        headcounts=tuple(buckets),
    )
    if criteria.empty:
        raise ValueError("Add a search description or at least one filter")
    return criteria


class CompanyDiscoveryService:
    def __init__(
        self,
        session: Session,
        tenant: TenantContext,
        provider: CompanyDiscoveryProvider,
        provider_registry: ProviderRegistry,
        now: datetime | None = None,
        extra_providers: Mapping[str, CompanyDiscoveryProvider] | None = None,
    ) -> None:
        self.session = session
        self.tenant = tenant
        self.provider = provider
        self.providers = _provider_map(provider, extra_providers)
        self.provider_registry = provider_registry
        self.now = _as_utc(now or datetime.now(UTC))

    # --- sources ----------------------------------------------------------------------

    def sources(self) -> list[dict[str, Any]]:
        require_permission(self.tenant, Permission.READ)
        used = _paid_searches_today(self.session, self.tenant.organization_id, self.now)
        output = []
        for key, provider in self.providers.items():
            status = provider.status() if hasattr(provider, "status") else {}
            item = {
                "key": key,
                "label": getattr(provider, "label", "Hunter Discover" if key == "HUNTER" else key),
                "configured": provider.configured,
                "costs_credits": _costs_credits(provider),
                "provider": status.get("provider"),
                "model": status.get("model"),
                "configuration_error": status.get("configuration_error"),
            }
            if _costs_credits(provider):
                item["daily_limit"] = ai_daily_limit()
                item["used_today"] = used
            output.append(item)
        return output

    def _source(self, key: str | None) -> CompanyDiscoveryProvider:
        if not key:
            return self.provider
        provider = self.providers.get(str(key).strip().upper())
        if provider is None:
            raise ValueError(f"Unknown discovery source: {key}")
        if _costs_credits(provider):
            require_permission(self.tenant, Permission.SPEND_PROVIDER_CREDITS)
        return provider

    # --- criteria and on-demand discovery ---------------------------------------------

    def suggest_criteria(self, product_id: str) -> dict[str, Any]:
        require_permission(self.tenant, Permission.READ)
        product = self._product(product_id)
        icp = self._active_icp(product.id)
        countries: list[str] = []
        keywords: list[str] = []
        employee_min = product.employee_min
        employee_max = product.employee_max
        if icp is not None:
            employee_min = icp.employee_min if icp.employee_min is not None else employee_min
            employee_max = icp.employee_max if icp.employee_max is not None else employee_max
            ordered = sorted(
                icp.criteria,
                key=lambda item: (-(item.weight or 0), item.kind or "", item.value or ""),
            )
            for criterion in ordered:
                kind = criterion.kind.strip().lower()
                value = criterion.value.strip()
                if kind in {"country", "location"} and _COUNTRY.match(value):
                    countries.append(value.upper())
                elif kind in {"industry", "keyword", "business_service"} and value:
                    keywords.append(value)
                elif kind == "employee_min" and value.isdigit():
                    employee_min = int(value)
                elif kind == "employee_max" and value.isdigit():
                    employee_max = int(value)
        return {
            "product_id": product.id,
            "icp_id": icp.id if icp else None,
            "query": None,
            "countries": _clean_list(countries),
            "industries": [],
            "keywords": _clean_list(keywords),
            "headcounts": list(headcount_buckets(employee_min, employee_max)),
            "provider_configured": self.provider.configured,
            "sources": self.sources(),
        }

    def preview(
        self, criteria: DiscoveryCriteria, provider_key: str | None = None
    ) -> dict[str, Any]:
        require_permission(self.tenant, Permission.IMPORT_COMPANIES)
        source = self._source(provider_key)
        organization_id = self.tenant.organization_id
        _check_budget(self.session, organization_id, source, self.now)
        spend = _start_spend(
            self.session,
            organization_id=organization_id,
            actor_user_id=self.tenant.actor_user_id,
            entity_id=organization_id,
            context="preview",
            provider=source,
            now=self.now,
        )
        result, notes = _discover(
            source, criteria, 0, exclude_domains=_tenant_domains(self.session, organization_id)
        )
        _finish_spend(spend, result)
        known = _known_domains(self.session, organization_id, result.companies)
        return {
            "provider_key": result.provider_key,
            "total_results": result.total_results,
            "notes": notes,
            "companies": [
                {
                    "domain": item.domain,
                    "name": item.name,
                    "already_in_list": item.domain in known,
                    "source_urls": list(item.source_urls),
                    "rationale": item.rationale,
                }
                for item in result.companies
            ],
        }

    def add(self, companies: list[dict[str, str]]) -> dict[str, Any]:
        require_permission(self.tenant, Permission.IMPORT_COMPANIES)
        if len(companies) > 100:
            raise ValueError("Add at most 100 companies at a time")
        candidates = []
        for item in companies:
            domain = str(item.get("domain") or "").strip()
            name = str(item.get("name") or "").strip() or domain
            if domain:
                candidates.append(DiscoveredCompany(domain=domain, name=name[:255]))
        added, known, invalid = _attach_new(
            self.session, self.tenant, candidates, max_new=len(candidates)
        )
        self._audit(
            "company_discovery.manual_add",
            "company_discovery",
            self.tenant.organization_id,
            {"added": len(added), "already_known": known, "invalid": invalid},
        )
        return {
            "added": [_company_output(item) for item in added],
            "already_known": known,
            "invalid": invalid,
        }

    # --- scheduled policies ------------------------------------------------------------

    def list(self) -> list[CompanyDiscoveryPolicy]:
        require_permission(self.tenant, Permission.READ)
        return list(
            self.session.scalars(
                select(CompanyDiscoveryPolicy)
                .where(CompanyDiscoveryPolicy.organization_id == self.tenant.organization_id)
                .order_by(CompanyDiscoveryPolicy.created_at.desc(), CompanyDiscoveryPolicy.id)
            )
        )

    def get(self, policy_id: str) -> CompanyDiscoveryPolicy:
        require_permission(self.tenant, Permission.READ)
        policy = self.session.scalar(
            select(CompanyDiscoveryPolicy).where(
                CompanyDiscoveryPolicy.id == policy_id,
                CompanyDiscoveryPolicy.organization_id == self.tenant.organization_id,
            )
        )
        if policy is None:
            raise NotFoundError("Company discovery rule not found")
        return policy

    def create(self, values: dict[str, Any]) -> CompanyDiscoveryPolicy:
        require_permission(self.tenant, Permission.MANAGE_AUTOMATION_POLICIES)
        product = self._product(str(values.get("product_id") or ""))
        source = self._source(values.get("provider_key"))
        criteria = normalize_criteria(
            query=values.get("query"),
            countries=values.get("countries"),
            industries=values.get("industries"),
            keywords=values.get("keywords"),
            headcounts=values.get("headcounts"),
        )
        options = self._validated_options(values, defaults=None)
        name = str(values.get("name") or "").strip() or f"{product.name} discovery"
        policy = CompanyDiscoveryPolicy(
            organization_id=self.tenant.organization_id,
            product_id=product.id,
            name=name[:200],
            provider_key=source.key,
            query=criteria.query,
            countries=list(criteria.countries),
            industries=list(criteria.industries),
            keywords=list(criteria.keywords),
            headcounts=list(criteria.headcounts),
            next_offset=0,
            next_run_at=_as_utc(values.get("next_run_at") or self.now),
            run_as_user_id=self.tenant.actor_user_id,
            created_by_user_id=self.tenant.actor_user_id,
            **options,
        )
        self.session.add(policy)
        self.session.flush()
        self._audit_policy("company_discovery.policy.created", policy)
        return policy

    def update(self, policy_id: str, changes: dict[str, Any]) -> CompanyDiscoveryPolicy:
        require_permission(self.tenant, Permission.MANAGE_AUTOMATION_POLICIES)
        policy = self.get(policy_id)
        allowed = {
            "name",
            "query",
            "countries",
            "industries",
            "keywords",
            "headcounts",
            "max_new_per_run",
            "cadence_minutes",
            "enabled",
            "run_workflow",
            "allow_provider_credits",
            "next_run_at",
            "provider_key",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"Unsupported discovery fields: {', '.join(sorted(unknown))}")
        if changes.get("provider_key"):
            source = self._source(changes["provider_key"])
            if source.key != policy.provider_key:
                policy.provider_key = source.key
                policy.next_offset = 0
        criteria_keys = {"query", "countries", "industries", "keywords", "headcounts"}
        if criteria_keys & set(changes):
            current = policy.criteria()
            merged = {
                "query": changes.get("query", current.query),
                "countries": changes.get("countries", list(current.countries)),
                "industries": changes.get("industries", list(current.industries)),
                "keywords": changes.get("keywords", list(current.keywords)),
                "headcounts": changes.get("headcounts", list(current.headcounts)),
            }
            criteria = normalize_criteria(**merged)
            policy.query = criteria.query
            policy.countries = list(criteria.countries)
            policy.industries = list(criteria.industries)
            policy.keywords = list(criteria.keywords)
            policy.headcounts = list(criteria.headcounts)
            policy.next_offset = 0
        options = self._validated_options(changes, defaults=policy)
        for key, value in options.items():
            if key in changes:
                setattr(policy, key, value)
        if "name" in changes and str(changes["name"] or "").strip():
            policy.name = str(changes["name"]).strip()[:200]
        if changes.get("next_run_at") is not None:
            policy.next_run_at = _as_utc(changes["next_run_at"])
        policy.updated_at = self.now
        self.session.flush()
        self._audit_policy("company_discovery.policy.updated", policy)
        return policy

    def runs(self, policy_id: str) -> list[CompanyDiscoveryRun]:
        policy = self.get(policy_id)
        return list(
            self.session.scalars(
                select(CompanyDiscoveryRun)
                .where(
                    CompanyDiscoveryRun.organization_id == self.tenant.organization_id,
                    CompanyDiscoveryRun.policy_id == policy.id,
                )
                .order_by(CompanyDiscoveryRun.started_at.desc(), CompanyDiscoveryRun.id.desc())
                .limit(50)
            )
        )

    def run_now(self, policy_id: str) -> CompanyDiscoveryRun:
        require_permission(self.tenant, Permission.MANAGE_AUTOMATION_POLICIES)
        policy = self.get(policy_id)
        executor = CompanyDiscoveryExecutor(
            self.session,
            self.provider,
            self.provider_registry,
            now=self.now,
            extra_providers=self.providers,
        )
        run = executor.execute(
            policy,
            scheduled_for=self.now,
            triggered_by_user_id=self.tenant.actor_user_id,
            advance_schedule=False,
        )
        if run is None:
            raise ValueError("A run for this rule is already in progress")
        return run

    # --- helpers -----------------------------------------------------------------------

    def _validated_options(
        self, values: dict[str, Any], *, defaults: CompanyDiscoveryPolicy | None
    ) -> dict[str, Any]:
        def pick(key: str, fallback: Any) -> Any:
            if key in values and values[key] is not None:
                return values[key]
            return getattr(defaults, key) if defaults is not None else fallback

        cadence = int(pick("cadence_minutes", 1440))
        max_new = int(pick("max_new_per_run", 20))
        if not MIN_CADENCE_MINUTES <= cadence <= MAX_CADENCE_MINUTES:
            raise ValueError("cadence_minutes must be between 60 and 43200")
        if not 1 <= max_new <= 100:
            raise ValueError("max_new_per_run must be between 1 and 100")
        return {
            "cadence_minutes": cadence,
            "max_new_per_run": max_new,
            "enabled": bool(pick("enabled", True)),
            "run_workflow": bool(pick("run_workflow", True)),
            "allow_provider_credits": bool(pick("allow_provider_credits", False)),
        }

    def _product(self, product_id: str) -> Product:
        product = self.session.scalar(
            select(Product).where(
                Product.id == product_id,
                Product.organization_id == self.tenant.organization_id,
            )
        )
        if product is None:
            raise NotFoundError("Product not found")
        return product

    def _active_icp(self, product_id: str) -> ICP | None:
        return self.session.scalar(
            select(ICP)
            .where(
                ICP.product_id == product_id,
                ICP.organization_id == self.tenant.organization_id,
                ICP.is_active.is_(True),
            )
            .order_by(ICP.version.desc())
            .limit(1)
        )

    def _audit(self, action: str, entity_type: str, entity_id: str, metadata: dict) -> None:
        self.session.add(
            AuditLog(
                organization_id=self.tenant.organization_id,
                actor_user_id=self.tenant.actor_user_id,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                metadata_json=metadata,
            )
        )
        self.session.flush()

    def _audit_policy(self, action: str, policy: CompanyDiscoveryPolicy) -> None:
        self._audit(
            action,
            "company_discovery_policy",
            policy.id,
            {
                "product_id": policy.product_id,
                "enabled": policy.enabled,
                "cadence_minutes": policy.cadence_minutes,
                "max_new_per_run": policy.max_new_per_run,
                "run_workflow": policy.run_workflow,
                "allow_provider_credits": policy.allow_provider_credits,
                "provider_key": policy.provider_key,
            },
        )


class CompanyDiscoveryExecutor:
    """Cron-safe executor. Each run re-checks the stored run-as user's current permissions."""

    def __init__(
        self,
        session: Session,
        provider: CompanyDiscoveryProvider,
        provider_registry: ProviderRegistry,
        now: datetime | None = None,
        extra_providers: Mapping[str, CompanyDiscoveryProvider] | None = None,
    ) -> None:
        self.session = session
        self.provider = provider
        self.providers = _provider_map(provider, extra_providers)
        self.provider_registry = provider_registry
        self.now = _as_utc(now or datetime.now(UTC))

    def run_due(
        self,
        *,
        organization_id: str | None = None,
        limit: int = 10,
        triggered_by_user_id: str | None = None,
    ) -> list[CompanyDiscoveryRun]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        query = select(CompanyDiscoveryPolicy).where(
            CompanyDiscoveryPolicy.enabled.is_(True),
            CompanyDiscoveryPolicy.next_run_at <= self.now,
        )
        if organization_id is not None:
            query = query.where(CompanyDiscoveryPolicy.organization_id == organization_id)
        policies = list(
            self.session.scalars(
                query.order_by(CompanyDiscoveryPolicy.next_run_at, CompanyDiscoveryPolicy.id)
                .limit(limit)
            )
        )
        runs = []
        for policy in policies:
            run = self.execute(
                policy,
                scheduled_for=_as_utc(policy.next_run_at),
                triggered_by_user_id=triggered_by_user_id,
            )
            if run is not None:
                runs.append(run)
        return runs

    def execute(
        self,
        policy: CompanyDiscoveryPolicy,
        *,
        scheduled_for: datetime,
        triggered_by_user_id: str | None,
        advance_schedule: bool = True,
    ) -> CompanyDiscoveryRun | None:
        run = CompanyDiscoveryRun(
            organization_id=policy.organization_id,
            policy_id=policy.id,
            scheduled_for=_as_utc(scheduled_for),
            started_at=self.now,
            status=CompanyDiscoveryRunStatus.RUNNING,
            added_relationship_ids=[],
            notes=[],
            run_as_user_id=policy.run_as_user_id,
            triggered_by_user_id=triggered_by_user_id,
            candidates_found=0,
            companies_added=0,
            already_known=0,
            workflows_completed=0,
            workflows_failed=0,
        )
        try:
            with self.session.begin_nested():
                self.session.add(run)
                self.session.flush()
        except IntegrityError:
            if advance_schedule:
                policy.next_run_at = _next_run_after(
                    scheduled_for, policy.cadence_minutes, self.now
                )
                self.session.flush()
            return None

        policy.last_run_at = self.now
        if advance_schedule:
            policy.next_run_at = _next_run_after(scheduled_for, policy.cadence_minutes, self.now)

        source = self.providers.get((policy.provider_key or self.provider.key).upper())
        if source is None:
            self._fail(policy, run, "DISCOVERY_UNKNOWN_SOURCE")
            return self._finish(policy, run)
        tenant = self._execution_tenant(policy, source)
        if tenant is None:
            self._fail(policy, run, "DISCOVERY_RUN_AS_PERMISSION_DENIED")
            return self._finish(policy, run)

        spend = None
        try:
            _check_budget(self.session, policy.organization_id, source, self.now)
            spend = _start_spend(
                self.session,
                organization_id=policy.organization_id,
                actor_user_id=run.triggered_by_user_id or policy.run_as_user_id,
                entity_id=policy.id,
                context="rule",
                provider=source,
                now=self.now,
            )
            result, notes = _discover(
                source,
                policy.criteria(),
                policy.next_offset or 0,
                exclude_domains=_tenant_domains(self.session, policy.organization_id),
            )
        except ProviderNotConfigured:
            self._fail(policy, run, "DISCOVERY_PROVIDER_NOT_CONFIGURED")
            return self._finish(policy, run)
        except ProviderError as error:
            self._fail(policy, run, f"DISCOVERY_{getattr(error, 'category', 'PROVIDER_ERROR')}")
            return self._finish(policy, run)
        except ValueError:
            self._fail(policy, run, "DISCOVERY_INVALID_CRITERIA")
            return self._finish(policy, run)

        _finish_spend(spend, result)
        if any("pagination" in note.lower() for note in notes):
            policy.next_offset = 0
        added, known, invalid = _attach_new(
            self.session, tenant, list(result.companies), max_new=policy.max_new_per_run
        )
        run.candidates_found = len(result.companies)
        run.companies_added = len(added)
        run.already_known = known
        run.added_relationship_ids = [item.id for item in added]
        consumed_page = len(added) + known + invalid >= len(result.companies)
        if consumed_page and result.companies:
            next_offset = (policy.next_offset or 0) + len(result.companies)
            has_more = result.total_results is not None and next_offset < result.total_results
            if has_more:
                policy.next_offset = next_offset
            elif not added:
                notes.append("No new companies left for these filters. Broaden the filters.")
        if not result.companies:
            notes.append("The search returned no companies. Broaden the filters.")

        if policy.run_workflow:
            for relationship in added:
                try:
                    with self.session.begin_nested():
                        AutomationOrchestrationService(
                            self.session, tenant, self.provider_registry
                        ).run_until_gate(
                            relationship.id,
                            policy.product_id,
                            allow_provider_credits=policy.allow_provider_credits,
                            contact_provider="HUNTER",
                            max_contacts=10,
                            max_steps=4,
                        )
                    run.workflows_completed += 1
                except Exception:
                    run.workflows_failed += 1
            if run.workflows_failed:
                notes.append(
                    "Some workflows stopped early; open the company to see which step needs input."
                )

        run.notes = notes[:10]
        run.status = CompanyDiscoveryRunStatus.COMPLETED
        run.completed_at = self.now
        policy.last_status = run.status.value
        policy.last_error_code = None
        return self._finish(policy, run)

    def _execution_tenant(
        self, policy: CompanyDiscoveryPolicy, source: CompanyDiscoveryProvider
    ) -> TenantContext | None:
        user = self.session.get(User, policy.run_as_user_id)
        membership = self.session.scalar(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == policy.organization_id,
                OrganizationMembership.user_id == policy.run_as_user_id,
            )
        )
        if user is None or not user.is_active or membership is None:
            return None
        required = {Permission.IMPORT_COMPANIES, Permission.WRITE_CATALOG}
        if policy.run_workflow:
            required |= {
                Permission.RUN_PUBLIC_RESEARCH,
                Permission.RUN_FIT_ASSESSMENT,
                Permission.RUN_QUALIFICATION,
            }
        if policy.allow_provider_credits or _costs_credits(source):
            required.add(Permission.SPEND_PROVIDER_CREDITS)
        if not required.issubset(ROLE_PERMISSIONS[membership.role]):
            return None
        return TenantContext(policy.organization_id, policy.run_as_user_id, membership.role)

    def _fail(self, policy: CompanyDiscoveryPolicy, run: CompanyDiscoveryRun, code: str) -> None:
        run.status = CompanyDiscoveryRunStatus.FAILED
        run.error_code = code[:100]
        run.completed_at = self.now
        policy.last_status = run.status.value
        policy.last_error_code = run.error_code

    def _finish(
        self, policy: CompanyDiscoveryPolicy, run: CompanyDiscoveryRun
    ) -> CompanyDiscoveryRun:
        self.session.add(
            AuditLog(
                organization_id=policy.organization_id,
                actor_user_id=run.triggered_by_user_id or policy.run_as_user_id,
                action="company_discovery.run.completed",
                entity_type="company_discovery_run",
                entity_id=run.id,
                metadata_json={
                    "policy_id": policy.id,
                    "status": run.status.value,
                    "error_code": run.error_code,
                    "candidates_found": run.candidates_found,
                    "companies_added": run.companies_added,
                    "workflows_completed": run.workflows_completed,
                    "workflows_failed": run.workflows_failed,
                },
            )
        )
        self.session.flush()
        return run


def _discover(
    provider: CompanyDiscoveryProvider,
    criteria: DiscoveryCriteria,
    offset: int,
    *,
    exclude_domains: tuple[str, ...] = (),
) -> tuple[DiscoveryResult, list[str]]:
    if getattr(provider, "accepts_exclusions", False):
        result = provider.discover(criteria, offset=offset, exclude_domains=exclude_domains)
        return result, list(result.notes)
    # Imported lazily so the application layer does not depend on the adapter at import time.
    from marketingiq.infrastructure.discovery import (
        DiscoveryInvalidFilter,
        DiscoveryPaginationUnavailable,
    )

    notes: list[str] = []
    try:
        return provider.discover(criteria, offset=offset), notes
    except DiscoveryPaginationUnavailable:
        notes.append(
            "Your Hunter plan returns only the first page of results (pagination unavailable)."
        )
        return provider.discover(criteria, offset=0), notes
    except DiscoveryInvalidFilter as error:
        if getattr(error, "code", None) != "invalid_industry" or not criteria.industries:
            raise
        notes.append("Industry names were not recognised by Hunter; searched them as keywords.")
        relaxed = DiscoveryCriteria(
            query=criteria.query,
            countries=criteria.countries,
            industries=(),
            keywords=tuple(dict.fromkeys(criteria.keywords + criteria.industries)),
            headcounts=criteria.headcounts,
        )
        return provider.discover(relaxed, offset=offset), notes


def _known_domains(
    session: Session, organization_id: str, companies: tuple[DiscoveredCompany, ...]
) -> set[str]:
    normalized = {}
    for item in companies:
        try:
            normalized[normalize_domain(item.domain)] = item.domain
        except ValueError:
            continue
    if not normalized:
        return set()
    rows = session.execute(
        select(CompanyIdentifier.normalized_value)
        .join(
            OrganizationCompany,
            OrganizationCompany.company_id == CompanyIdentifier.company_id,
        )
        .where(
            OrganizationCompany.organization_id == organization_id,
            CompanyIdentifier.kind == "DOMAIN",
            CompanyIdentifier.normalized_value.in_(list(normalized)),
        )
    ).all()
    return {normalized[row[0]] for row in rows if row[0] in normalized}


def _attach_new(
    session: Session,
    tenant: TenantContext,
    candidates: list[DiscoveredCompany],
    *,
    max_new: int,
) -> tuple[list[OrganizationCompany], int, int]:
    known_domains = _known_domains(session, tenant.organization_id, tuple(candidates))
    service = CompanyService(session, tenant)
    added: list[OrganizationCompany] = []
    known = 0
    invalid = 0
    seen: set[str] = set()
    for item in candidates:
        if len(added) >= max_new:
            break
        try:
            normalized = normalize_domain(item.domain)
        except ValueError:
            invalid += 1
            continue
        if normalized in seen or item.domain in known_domains:
            known += 1
            continue
        seen.add(normalized)
        try:
            with session.begin_nested():
                relationship = service.attach_company(
                    domain=item.domain,
                    canonical_name=item.name or item.domain,
                    lifecycle_status=DISCOVERED_LIFECYCLE,
                    shared_values={"website_url": f"https://{normalized}"},
                )
        except (ValueError, IntegrityError):
            invalid += 1
            continue
        added.append(relationship)
    return added, known, invalid


def _company_output(item: OrganizationCompany) -> dict[str, Any]:
    domain = next(
        (value.normalized_value for value in item.company.identifiers if value.kind == "DOMAIN"),
        None,
    )
    return {"id": item.id, "canonical_name": item.company.canonical_name, "domain": domain}
