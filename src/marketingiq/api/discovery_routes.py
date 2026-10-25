from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from marketingiq.application.discovery import MAX_RESULTS_LIMIT, CompanyDiscoveryService
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.discovery import CompanyDiscoveryProvider, DiscoveryCandidateStatus


class DiscoveryRunRequest(BaseModel):
    max_results: int = Field(default=10, ge=1, le=MAX_RESULTS_LIMIT)


class CandidateReviewRequest(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


def register_discovery_routes(
    app: FastAPI,
    prefix: str,
    tenant_dependency: Callable[..., TenantContext],
    session_dependency: Callable[..., Session],
    provider_getter: Callable[[], CompanyDiscoveryProvider],
) -> None:
    def discovery_service(
        context: TenantContext = Depends(tenant_dependency),
        db: Session = Depends(session_dependency),
    ) -> CompanyDiscoveryService:
        return CompanyDiscoveryService(db, context, provider_getter())

    @app.get(prefix + "/discovery/status")
    def discovery_status(svc: CompanyDiscoveryService = Depends(discovery_service)):
        return svc.status()

    @app.post(prefix + "/products/{product_id}/discovery-runs", status_code=201)
    def start_discovery_run(
        product_id: str,
        body: DiscoveryRunRequest,
        svc: CompanyDiscoveryService = Depends(discovery_service),
    ):
        run = svc.run(product_id, max_results=body.max_results)
        return _run_output(run, include_candidates=True)

    @app.get(prefix + "/discovery-runs")
    def list_discovery_runs(
        product_id: str | None = None,
        svc: CompanyDiscoveryService = Depends(discovery_service),
    ):
        return [_run_output(run) for run in svc.list_runs(product_id)]

    @app.get(prefix + "/discovery-runs/{run_id}")
    def get_discovery_run(
        run_id: str,
        svc: CompanyDiscoveryService = Depends(discovery_service),
    ):
        return _run_output(svc.get_run(run_id), include_candidates=True)

    @app.get(prefix + "/discovery-candidates")
    def list_discovery_candidates(
        status: DiscoveryCandidateStatus | None = DiscoveryCandidateStatus.PROPOSED,
        product_id: str | None = None,
        limit: int = 100,
        svc: CompanyDiscoveryService = Depends(discovery_service),
    ):
        return [
            _candidate_output(item)
            for item in svc.list_candidates(status=status, product_id=product_id, limit=limit)
        ]

    @app.post(prefix + "/discovery-candidates/{candidate_id}/accept")
    def accept_discovery_candidate(
        candidate_id: str,
        body: CandidateReviewRequest,
        svc: CompanyDiscoveryService = Depends(discovery_service),
    ):
        return _candidate_output(svc.accept(candidate_id, note=body.note))

    @app.post(prefix + "/discovery-candidates/{candidate_id}/reject")
    def reject_discovery_candidate(
        candidate_id: str,
        body: CandidateReviewRequest,
        svc: CompanyDiscoveryService = Depends(discovery_service),
    ):
        return _candidate_output(svc.reject(candidate_id, note=body.note))


def _run_output(run, *, include_candidates: bool = False) -> dict:
    output = {
        "id": run.id,
        "organization_id": run.organization_id,
        "product_id": run.product_id,
        "icp_id": run.icp_id,
        "icp_version": run.icp_version,
        "provider_key": run.provider_key,
        "model": run.model,
        "workflow_version": run.workflow_version,
        "status": run.status,
        "requested_max_results": run.requested_max_results,
        "excluded_domain_count": run.excluded_domain_count,
        "returned_count": run.returned_count,
        "proposed_count": run.proposed_count,
        "skipped_count": run.skipped_count,
        "searches_used": run.searches_used,
        "input_tokens": run.input_tokens,
        "output_tokens": run.output_tokens,
        "error_category": run.error_category,
        "initiated_by_user_id": run.initiated_by_user_id,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
    }
    if include_candidates:
        output["candidates"] = [_candidate_output(item) for item in run.candidates]
    return output


def _candidate_output(item) -> dict:
    return {
        "id": item.id,
        "run_id": item.run_id,
        "product_id": item.product_id,
        "position": item.position,
        "company_name": item.company_name,
        "domain": item.normalized_domain,
        "raw_domain": item.raw_domain,
        "country_code": item.country_code,
        "industry": item.industry,
        "rationale": item.rationale,
        "source_urls": item.source_urls,
        "status": item.status,
        "skip_reason": item.skip_reason,
        "classification": item.classification,
        "organization_company_id": item.organization_company_id,
        "reviewed_by_user_id": item.reviewed_by_user_id,
        "reviewed_at": item.reviewed_at,
        "review_note": item.review_note,
        "created_at": item.created_at,
    }
