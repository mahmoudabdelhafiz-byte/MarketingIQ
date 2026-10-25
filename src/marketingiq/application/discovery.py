from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from marketingiq.application.authorization import Permission, require_permission
from marketingiq.application.companies import CompanyService, normalize_domain
from marketingiq.application.errors import ConflictError, NotFoundError
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.discovery import (
    CompanyDiscoveryCandidate,
    CompanyDiscoveryProvider,
    CompanyDiscoveryRun,
    DiscoveredCompany,
    DiscoveryCandidateStatus,
    DiscoveryCriterion,
    DiscoveryProviderError,
    DiscoveryRequest,
    DiscoveryRunStatus,
    DiscoverySkipReason,
)
from marketingiq.domain.models import (
    ICP,
    AuditLog,
    CompanyIdentifier,
    OrganizationCompany,
    Product,
    ProductStatus,
)

WORKFLOW_VERSION = "company-discovery-v1"
DISCOVERED_LIFECYCLE_STATUS = "DISCOVERED"
MAX_RESULTS_LIMIT = 25
MAX_EXCLUDED_DOMAINS = 200
MAX_SOURCE_URLS = 5
MAX_RATIONALE_CHARS = 1000


def normalize_source_url(value: str) -> str | None:
    """Comparable form of a public HTTP(S) URL; None when it is not one."""

    try:
        parsed = urlsplit(value.strip())
    except (AttributeError, ValueError):
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    host = parsed.hostname.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path.rstrip("/")
    return urlunsplit(("https", host, path, parsed.query, ""))


class CompanyDiscoveryService:
    """Tenant-scoped discovery of candidate companies for a product's active ICP.

    A run spends provider budget, so it requires SPEND_PROVIDER_CREDITS and a configurable daily
    run cap. Discovery never writes to the company catalog: candidates stay PROPOSED until a
    member with WRITE_CATALOG accepts them, which attaches the company through CompanyService.
    """

    def __init__(
        self,
        session: Session,
        tenant: TenantContext,
        provider: CompanyDiscoveryProvider,
        *,
        now: datetime | None = None,
        daily_run_limit: int | None = None,
    ) -> None:
        if not tenant.organization_id or not tenant.actor_user_id:
            raise ValueError("Tenant and actor are required")
        self.session = session
        self.tenant = tenant
        self.provider = provider
        self._now = now
        if daily_run_limit is None:
            daily_run_limit = int(os.environ.get("DISCOVERY_DAILY_RUN_LIMIT", "5"))
        if daily_run_limit < 1:
            raise ValueError("DISCOVERY_DAILY_RUN_LIMIT must be at least 1")
        self.daily_run_limit = daily_run_limit

    def now(self) -> datetime:
        return self._now or datetime.now(UTC)

    # ----------------------------------------------------------------- status

    def status(self) -> dict:
        require_permission(self.tenant, Permission.READ)
        day_start = self._day_start()
        runs_today = self._runs_since(day_start)
        return {
            "provider_key": self.provider.key,
            "configured": self.provider.configured,
            "model": self.provider.model,
            "configuration_error": getattr(self.provider, "configuration_error", None),
            "workflow_version": WORKFLOW_VERSION,
            "daily_run_limit": self.daily_run_limit,
            "runs_today": runs_today,
            "runs_remaining_today": max(self.daily_run_limit - runs_today, 0),
        }

    # -------------------------------------------------------------------- run

    def run(self, product_id: str, *, max_results: int = 10) -> CompanyDiscoveryRun:
        require_permission(self.tenant, Permission.SPEND_PROVIDER_CREDITS)
        if not 1 <= max_results <= MAX_RESULTS_LIMIT:
            raise ValueError(f"max_results must be between 1 and {MAX_RESULTS_LIMIT}")
        product = self._product(product_id)
        if product.status == ProductStatus.ARCHIVED:
            raise ConflictError("Archived products cannot be used for discovery")
        icp = self._active_icp(product.id)
        if not self.provider.configured:
            reason = getattr(self.provider, "configuration_error", None)
            raise ConflictError(
                "Company discovery provider is not configured"
                + (f": {reason}" if reason else "")
            )
        if self._runs_since(self._day_start()) >= self.daily_run_limit:
            raise ConflictError("Daily company discovery run limit reached")

        attached = self._attached_domains()
        rejected = self._domains_with_status(DiscoveryCandidateStatus.REJECTED)
        proposed = self._domains_with_status(DiscoveryCandidateStatus.PROPOSED)
        excluded = sorted(attached | rejected | proposed)[:MAX_EXCLUDED_DOMAINS]

        run = CompanyDiscoveryRun(
            organization_id=self.tenant.organization_id,
            product_id=product.id,
            icp_id=icp.id,
            icp_version=icp.version,
            provider_key=self.provider.key,
            model=self.provider.model,
            workflow_version=WORKFLOW_VERSION,
            status=DiscoveryRunStatus.RUNNING,
            requested_max_results=max_results,
            excluded_domain_count=len(excluded),
            initiated_by_user_id=self.tenant.actor_user_id,
            started_at=self.now(),
        )
        self.session.add(run)
        self.session.flush()

        request = DiscoveryRequest(
            product_name=product.name,
            product_description=product.description,
            value_proposition=product.value_proposition,
            criteria=tuple(
                DiscoveryCriterion(kind=item.kind, value=item.value, required=item.required)
                for item in sorted(icp.criteria, key=lambda c: (not c.required, -c.weight, c.kind))
            ),
            employee_min=icp.employee_min or product.employee_min,
            employee_max=icp.employee_max or product.employee_max,
            exclude_domains=tuple(excluded),
            max_results=max_results,
        )
        try:
            result = self.provider.discover(request)
        except DiscoveryProviderError as error:
            run.status = DiscoveryRunStatus.FAILED
            run.error_category = error.category
            run.completed_at = self.now()
            self._audit_run(run)
            return run

        run.model = result.model or run.model
        run.searches_used = result.searches_used
        run.input_tokens = result.input_tokens
        run.output_tokens = result.output_tokens
        companies = result.companies[:max_results]
        run.returned_count = len(companies)
        retrieved = {
            normalized
            for normalized in (normalize_source_url(url) for url in result.retrieved_urls)
            if normalized
        }
        seen: set[str] = set()
        for position, company in enumerate(companies):
            candidate = self._candidate(
                run, product.id, position, company, retrieved, attached, rejected, proposed, seen
            )
            self.session.add(candidate)
            if candidate.status == DiscoveryCandidateStatus.PROPOSED:
                run.proposed_count += 1
            else:
                run.skipped_count += 1
        run.status = DiscoveryRunStatus.COMPLETED
        run.completed_at = self.now()
        self.session.flush()
        self._audit_run(run)
        return run

    def _candidate(
        self,
        run: CompanyDiscoveryRun,
        product_id: str,
        position: int,
        company: DiscoveredCompany,
        retrieved: set[str],
        attached: set[str],
        rejected: set[str],
        proposed: set[str],
        seen: set[str],
    ) -> CompanyDiscoveryCandidate:
        try:
            domain: str | None = normalize_domain(company.domain)
        except (ValueError, AttributeError):
            domain = None

        grounded: list[str] = []
        for url in company.source_urls:
            normalized = normalize_source_url(url)
            if normalized and normalized in retrieved and url.strip() not in grounded:
                grounded.append(url.strip()[:2048])
        grounded = grounded[:MAX_SOURCE_URLS]

        reason: DiscoverySkipReason | None = None
        if domain is None:
            reason = DiscoverySkipReason.INVALID_DOMAIN
        elif not grounded:
            reason = DiscoverySkipReason.UNGROUNDED
        elif domain in seen:
            reason = DiscoverySkipReason.DUPLICATE_IN_RUN
        elif domain in attached:
            reason = DiscoverySkipReason.ALREADY_ATTACHED
        elif domain in rejected:
            reason = DiscoverySkipReason.PREVIOUSLY_REJECTED
        elif domain in proposed:
            reason = DiscoverySkipReason.ALREADY_PROPOSED
        if domain:
            seen.add(domain)

        country = (company.country_code or "").strip().upper()
        return CompanyDiscoveryCandidate(
            organization_id=self.tenant.organization_id,
            run=run,
            product_id=product_id,
            position=position,
            company_name=(company.name or "").strip()[:255] or (domain or "Unknown company"),
            raw_domain=(company.domain or "").strip()[:255],
            normalized_domain=domain,
            country_code=country if len(country) == 2 and country.isalpha() else None,
            industry=(company.industry or "").strip()[:200] or None,
            rationale=(company.rationale or "").strip()[:MAX_RATIONALE_CHARS] or None,
            source_urls=grounded,
            status=DiscoveryCandidateStatus.SKIPPED
            if reason
            else DiscoveryCandidateStatus.PROPOSED,
            skip_reason=reason,
            created_at=self.now(),
        )

    # ---------------------------------------------------------------- reading

    def list_runs(self, product_id: str | None = None) -> list[CompanyDiscoveryRun]:
        require_permission(self.tenant, Permission.READ)
        query = select(CompanyDiscoveryRun).where(
            CompanyDiscoveryRun.organization_id == self.tenant.organization_id
        )
        if product_id:
            query = query.where(CompanyDiscoveryRun.product_id == product_id)
        return list(
            self.session.scalars(
                query.order_by(CompanyDiscoveryRun.started_at.desc(), CompanyDiscoveryRun.id)
            )
        )

    def get_run(self, run_id: str) -> CompanyDiscoveryRun:
        require_permission(self.tenant, Permission.READ)
        run = self.session.scalar(
            select(CompanyDiscoveryRun)
            .options(selectinload(CompanyDiscoveryRun.candidates))
            .where(
                CompanyDiscoveryRun.id == run_id,
                CompanyDiscoveryRun.organization_id == self.tenant.organization_id,
            )
        )
        if run is None:
            raise NotFoundError("discovery run not found")
        return run

    def list_candidates(
        self,
        *,
        status: DiscoveryCandidateStatus | None = DiscoveryCandidateStatus.PROPOSED,
        product_id: str | None = None,
        limit: int = 100,
    ) -> list[CompanyDiscoveryCandidate]:
        require_permission(self.tenant, Permission.READ)
        query = select(CompanyDiscoveryCandidate).where(
            CompanyDiscoveryCandidate.organization_id == self.tenant.organization_id
        )
        if status is not None:
            query = query.where(CompanyDiscoveryCandidate.status == status)
        if product_id:
            query = query.where(CompanyDiscoveryCandidate.product_id == product_id)
        return list(
            self.session.scalars(
                query.order_by(
                    CompanyDiscoveryCandidate.created_at.desc(),
                    CompanyDiscoveryCandidate.position,
                ).limit(max(1, min(limit, 500)))
            )
        )

    # ----------------------------------------------------------------- review

    def accept(self, candidate_id: str, *, note: str | None = None) -> CompanyDiscoveryCandidate:
        require_permission(self.tenant, Permission.WRITE_CATALOG)
        candidate = self._reviewable(candidate_id)
        if candidate.normalized_domain is None:  # PROPOSED implies a valid domain
            raise ConflictError("Candidate has no valid domain")
        # Only the verified domain and name become identity. AI-suggested country/industry stay
        # on the candidate; research providers establish evidenced facts after acceptance.
        relationship = CompanyService(self.session, self.tenant).attach_company(
            domain=candidate.normalized_domain,
            canonical_name=candidate.company_name,
            lifecycle_status=DISCOVERED_LIFECYCLE_STATUS,
        )
        candidate.status = DiscoveryCandidateStatus.ACCEPTED
        candidate.organization_company_id = relationship.id
        self._mark_reviewed(candidate, note)
        self._audit(
            "company_discovery.candidate_accepted",
            candidate,
            {"run_id": candidate.run_id, "organization_company_id": relationship.id},
        )
        return candidate

    def reject(self, candidate_id: str, *, note: str | None = None) -> CompanyDiscoveryCandidate:
        require_permission(self.tenant, Permission.WRITE_CATALOG)
        candidate = self._reviewable(candidate_id)
        candidate.status = DiscoveryCandidateStatus.REJECTED
        self._mark_reviewed(candidate, note)
        self._audit("company_discovery.candidate_rejected", candidate, {"run_id": candidate.run_id})
        return candidate

    # ---------------------------------------------------------------- helpers

    def _reviewable(self, candidate_id: str) -> CompanyDiscoveryCandidate:
        candidate = self.session.scalar(
            select(CompanyDiscoveryCandidate).where(
                CompanyDiscoveryCandidate.id == candidate_id,
                CompanyDiscoveryCandidate.organization_id == self.tenant.organization_id,
            )
        )
        if candidate is None:
            raise NotFoundError("discovery candidate not found")
        if candidate.status != DiscoveryCandidateStatus.PROPOSED:
            raise ConflictError(f"Candidate is {candidate.status.value}, not PROPOSED")
        return candidate

    def _mark_reviewed(self, candidate: CompanyDiscoveryCandidate, note: str | None) -> None:
        candidate.reviewed_by_user_id = self.tenant.actor_user_id
        candidate.reviewed_at = self.now()
        candidate.review_note = (note or "").strip()[:2000] or None

    def _product(self, product_id: str) -> Product:
        product = self.session.scalar(
            select(Product).where(
                Product.id == product_id,
                Product.organization_id == self.tenant.organization_id,
            )
        )
        if product is None:
            raise NotFoundError("product not found")
        return product

    def _active_icp(self, product_id: str) -> ICP:
        icp = self.session.scalar(
            select(ICP)
            .options(selectinload(ICP.criteria))
            .where(
                ICP.organization_id == self.tenant.organization_id,
                ICP.product_id == product_id,
                ICP.is_active.is_(True),
            )
            .order_by(ICP.version.desc(), ICP.created_at.desc())
        )
        if icp is None:
            raise ConflictError("Product has no active ICP to discover against")
        return icp

    def _attached_domains(self) -> set[str]:
        return set(
            self.session.scalars(
                select(CompanyIdentifier.normalized_value)
                .join(
                    OrganizationCompany,
                    OrganizationCompany.company_id == CompanyIdentifier.company_id,
                )
                .where(
                    OrganizationCompany.organization_id == self.tenant.organization_id,
                    CompanyIdentifier.kind == "DOMAIN",
                )
            )
        )

    def _domains_with_status(self, status: DiscoveryCandidateStatus) -> set[str]:
        return {
            domain
            for domain in self.session.scalars(
                select(CompanyDiscoveryCandidate.normalized_domain).where(
                    CompanyDiscoveryCandidate.organization_id == self.tenant.organization_id,
                    CompanyDiscoveryCandidate.status == status,
                    CompanyDiscoveryCandidate.normalized_domain.is_not(None),
                )
            )
            if domain
        }

    def _day_start(self) -> datetime:
        now = self.now()
        return now.replace(hour=0, minute=0, second=0, microsecond=0)

    def _runs_since(self, since: datetime) -> int:
        return int(
            self.session.scalar(
                select(func.count(CompanyDiscoveryRun.id)).where(
                    CompanyDiscoveryRun.organization_id == self.tenant.organization_id,
                    CompanyDiscoveryRun.started_at >= since,
                    CompanyDiscoveryRun.started_at < since + timedelta(days=1),
                )
            )
            or 0
        )

    def _audit_run(self, run: CompanyDiscoveryRun) -> None:
        self.session.add(
            AuditLog(
                organization_id=self.tenant.organization_id,
                actor_user_id=self.tenant.actor_user_id,
                action="company_discovery.run",
                entity_type="company_discovery_run",
                entity_id=run.id,
                metadata_json={
                    "product_id": run.product_id,
                    "icp_id": run.icp_id,
                    "provider_key": run.provider_key,
                    "status": run.status.value,
                    "error_category": run.error_category,
                    "returned": run.returned_count,
                    "proposed": run.proposed_count,
                    "skipped": run.skipped_count,
                    "searches_used": run.searches_used,
                },
            )
        )

    def _audit(self, action: str, candidate: CompanyDiscoveryCandidate, metadata: dict) -> None:
        self.session.add(
            AuditLog(
                organization_id=self.tenant.organization_id,
                actor_user_id=self.tenant.actor_user_id,
                action=action,
                entity_type="company_discovery_candidate",
                entity_id=candidate.id,
                metadata_json=metadata,
            )
        )
