from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from marketingiq.application.authorization import Permission, require_permission
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.models import (
    CompanyFact,
    CompanyIdentifier,
    DataSource,
    Evidence,
    OrganizationCompany,
    ProviderUsage,
    ResearchMode,
    ResearchRun,
    ResearchStatus,
)
from marketingiq.domain.providers import CompanyEnrichmentProvider, ProviderError, ProviderResult

WORKFLOW_VERSION = "company-research-v1"


class ProviderRegistry:
    def __init__(self, providers: list[CompanyEnrichmentProvider]) -> None:
        self._providers = {provider.key: provider for provider in providers}
        self._priority = {"PUBLIC_WEB": 10, "HUNTER": 100}

    def get(self, key: str) -> CompanyEnrichmentProvider:
        try:
            return self._providers[key.upper()]
        except KeyError as error:
            raise ValueError(f"Unknown provider: {key}") from error

    def status(self) -> list[dict]:
        return [
            {
                "provider_key": provider.key,
                "capabilities": sorted(provider.capabilities),
                "enabled": True,
                "priority": self._priority.get(provider.key, 1000),
                "credentials_configured": provider.configured,
                "costs_credits": provider.costs_credits,
                "status": "available" if provider.configured else "not_configured",
            }
            for provider in sorted(
                self._providers.values(), key=lambda p: self._priority.get(p.key, 1000)
            )
        ]


class CompanyResearchService:
    def __init__(self, session: Session, tenant: TenantContext, registry: ProviderRegistry) -> None:
        self.session = session
        self.tenant = tenant
        self.registry = registry
        self.public_freshness = timedelta(
            hours=int(os.environ.get("PUBLIC_WEB_FRESHNESS_HOURS", "24"))
        )
        self.external_freshness = timedelta(
            hours=int(os.environ.get("EXTERNAL_PROVIDER_FRESHNESS_HOURS", "168"))
        )

    def _relationship(self, relationship_id: str) -> OrganizationCompany:
        relationship = self.session.scalar(
            select(OrganizationCompany).where(
                OrganizationCompany.id == relationship_id,
                OrganizationCompany.organization_id == self.tenant.organization_id,
            )
        )
        if relationship is None:
            raise LookupError("organization company not found")
        return relationship

    def run(
        self,
        relationship_id: str,
        mode: ResearchMode = ResearchMode.PUBLIC_ONLY,
        providers: list[str] | None = None,
        force_refresh: bool = False,
    ) -> ResearchRun:
        require_permission(self.tenant, Permission.RUN_PUBLIC_RESEARCH)
        if mode != ResearchMode.PUBLIC_ONLY:
            require_permission(self.tenant, Permission.RUN_EXTERNAL_RESEARCH)
        relationship = self._relationship(relationship_id)
        domain = self.session.scalar(
            select(CompanyIdentifier.normalized_value).where(
                CompanyIdentifier.company_id == relationship.company_id,
                CompanyIdentifier.kind == "DOMAIN",
                CompanyIdentifier.is_primary.is_(True),
            )
        )
        if not domain:
            raise ValueError("company has no primary domain")
        requested = {key.upper() for key in (providers or [])}
        unknown = requested - {item["provider_key"] for item in self.registry.status()}
        if unknown:
            raise ValueError(f"Unknown providers: {', '.join(sorted(unknown))}")
        keys: list[str] = []
        if mode != ResearchMode.EXTERNAL_ONLY:
            keys.append("PUBLIC_WEB")
        if mode != ResearchMode.PUBLIC_ONLY:
            # Selecting an external mode is explicit; an optional list further restricts adapters.
            external = requested or {"HUNTER"}
            keys.extend(key for key in ("HUNTER",) if key in external)

        now = datetime.now(UTC)
        run = ResearchRun(
            organization_id=self.tenant.organization_id,
            company_id=relationship.company_id,
            initiated_by_user_id=self.tenant.actor_user_id,
            purpose="COMPANY_ENRICHMENT",
            mode=mode,
            status=ResearchStatus.RUNNING,
            started_at=now,
            providers_attempted=[],
            providers_succeeded=[],
            workflow_version=WORKFLOW_VERSION,
        )
        self.session.add(run)
        self.session.flush()
        errors: list[str] = []
        attempted: list[str] = []
        succeeded: list[str] = []
        for key in keys:
            provider = self.registry.get(key)
            attempted.append(key)
            usage = ProviderUsage(
                provider_key=key,
                organization_id=self.tenant.organization_id,
                company_id=relationship.company_id,
                operation="enrich_company",
                requested_at=datetime.now(UTC),
            )
            self.session.add(usage)
            self.session.flush()
            freshness = self.public_freshness if key == "PUBLIC_WEB" else self.external_freshness
            cached = (
                None
                if force_refresh
                else self.session.scalar(
                    select(ProviderUsage)
                    .where(
                        ProviderUsage.id != usage.id,
                        ProviderUsage.provider_key == key,
                        ProviderUsage.organization_id == self.tenant.organization_id,
                        ProviderUsage.company_id == relationship.company_id,
                        ProviderUsage.operation == "enrich_company",
                        ProviderUsage.success.is_(True),
                        ProviderUsage.completed_at >= now - freshness,
                    )
                    .order_by(ProviderUsage.completed_at.desc())
                )
            )
            if cached:
                usage.cache_hit = True
                usage.success = True
                usage.response_status = "CACHED"
                usage.completed_at = datetime.now(UTC)
                usage.credits_used = 0
                succeeded.append(key)
                continue
            try:
                result = provider.enrich_company(domain)
                self._store_result(run, relationship, result)
                usage.success = True
                usage.response_status = "SUCCESS" if result.facts else "NO_RESULTS"
                usage.request_identifier = result.request_identifier
                usage.credits_used = result.credits_used
                usage.credits_remaining = result.credits_remaining
                succeeded.append(key)
            except ProviderError as error:
                usage.success = False
                usage.response_status = "FAILURE"
                usage.error_category = error.category
                errors.append(f"{key}: {error.category}")
            finally:
                usage.completed_at = datetime.now(UTC)
        run.providers_attempted = attempted
        run.providers_succeeded = succeeded
        run.error_summary = "; ".join(errors) or None
        run.completed_at = datetime.now(UTC)
        if not attempted or len(succeeded) == len(attempted):
            run.status = ResearchStatus.COMPLETED
        elif succeeded:
            run.status = ResearchStatus.PARTIAL
        else:
            run.status = ResearchStatus.FAILED
        relationship.company.last_researched_at = run.completed_at
        return run

    def _store_result(self, run, relationship, result: ProviderResult) -> None:
        source = self.session.scalar(
            select(DataSource).where(
                DataSource.provider_key == result.provider_key,
                DataSource.external_reference.is_(None),
            )
        )
        if source is None:
            source = DataSource(
                provider_key=result.provider_key,
                display_name="Public company website"
                if result.provider_key == "PUBLIC_WEB"
                else result.provider_key.title(),
            )
            self.session.add(source)
            self.session.flush()
        for observation in result.facts:
            fact = CompanyFact(
                company_id=relationship.company_id,
                organization_id=self.tenant.organization_id,
                fact_key=observation.key,
                value=observation.value,
                classification=result.classification,
                redistribution_status=result.redistribution_status,
                confidence=observation.confidence,
                observed_at=observation.retrieved_at,
                model_version=WORKFLOW_VERSION,
                research_run_id=run.id,
            )
            fact.evidences.append(
                Evidence(
                    data_source_id=source.id,
                    reference_url=observation.source_url,
                    reference_text=observation.reference_text,
                    retrieved_at=observation.retrieved_at,
                )
            )
            self.session.add(fact)

    def list_runs(self, relationship_id: str) -> list[ResearchRun]:
        require_permission(self.tenant, Permission.READ)
        relationship = self._relationship(relationship_id)
        return list(
            self.session.scalars(
                select(ResearchRun)
                .where(
                    ResearchRun.organization_id == self.tenant.organization_id,
                    ResearchRun.company_id == relationship.company_id,
                )
                .order_by(ResearchRun.started_at.desc())
            )
        )

    def usage_report(self) -> list[dict]:
        require_permission(self.tenant, Permission.READ)
        today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        rows = self.session.execute(
            select(
                ProviderUsage.provider_key,
                func.count(ProviderUsage.id),
                func.sum(ProviderUsage.success),
                func.sum(ProviderUsage.credits_used),
                func.max(ProviderUsage.requested_at),
            )
            .where(
                ProviderUsage.organization_id == self.tenant.organization_id,
                ProviderUsage.requested_at >= today,
            )
            .group_by(ProviderUsage.provider_key)
        ).all()
        reports = []
        for key, count, successful, credits, last in rows:
            remaining = self.session.scalar(
                select(ProviderUsage.credits_remaining)
                .where(
                    ProviderUsage.organization_id == self.tenant.organization_id,
                    ProviderUsage.provider_key == key,
                    ProviderUsage.credits_remaining.is_not(None),
                )
                .order_by(ProviderUsage.completed_at.desc())
                .limit(1)
            )
            reports.append({
                "provider_key": key,
                "requests_today": count,
                "successful": successful or 0,
                "failed": count - (successful or 0),
                "credits_used": credits,
                "credits_remaining": remaining,
                "last_request": last,
            })
        return reports
