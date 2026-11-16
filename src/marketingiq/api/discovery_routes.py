from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from marketingiq.application.discovery import CompanyDiscoveryService, normalize_criteria
from marketingiq.application.research import ProviderRegistry
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.providers import ProviderError
from marketingiq.infrastructure.ai_discovery import KEY as AI_SOURCE_KEY
from marketingiq.infrastructure.ai_discovery import build_ai_discovery_provider
from marketingiq.infrastructure.discovery import HunterDiscoveryProvider

SOURCE_PATTERN = r"^[A-Za-z_]{2,50}$"
PROVIDER_MESSAGES = {
    "AUTHENTICATION_ERROR": (
        "The search provider rejected the API key. Create a new key in the provider's console, "
        "update it in Setup Python App → Environment variables, then Restart."
    ),
    "RATE_LIMITED": "The search provider's rate limit was reached. Try again in a few minutes.",
    "INSUFFICIENT_BALANCE": "The AI provider account has no balance left.",
    "DAILY_LIMIT": "Today's AI search limit is used up (DISCOVERY_AI_DAILY_LIMIT).",
    "NOT_CONFIGURED": "This search source is not configured.",
    "NETWORK_ERROR": "The search provider could not be reached. Try again.",
    "PROVIDER_UNAVAILABLE": "The search provider is temporarily unavailable. Try again later.",
    "MALFORMED_RESPONSE": "The AI search returned an unusable answer. Try again.",
    "INCOMPLETE_RESPONSE": "The AI search did not finish. Try again with narrower filters.",
    "SEARCH_UNAVAILABLE": "DeepSeek search is unavailable right now. Try again later.",
}


class DiscoveryCriteriaRequest(BaseModel):
    query: str | None = Field(default=None, max_length=500)
    countries: list[str] = Field(default_factory=list, max_length=25)
    industries: list[str] = Field(default_factory=list, max_length=25)
    keywords: list[str] = Field(default_factory=list, max_length=25)
    headcounts: list[str] = Field(default_factory=list, max_length=8)


class DiscoveryPreviewRequest(DiscoveryCriteriaRequest):
    provider_key: str | None = Field(default=None, pattern=SOURCE_PATTERN)


class DiscoveredCompanyRequest(BaseModel):
    domain: str = Field(min_length=3, max_length=255)
    name: str | None = Field(default=None, max_length=255)


class DiscoveryAddRequest(BaseModel):
    companies: list[DiscoveredCompanyRequest] = Field(min_length=1, max_length=100)


class DiscoveryPolicyCreateRequest(DiscoveryCriteriaRequest):
    product_id: str = Field(min_length=1, max_length=36)
    provider_key: str | None = Field(default=None, pattern=SOURCE_PATTERN)
    name: str | None = Field(default=None, max_length=200)
    max_new_per_run: int = Field(default=20, ge=1, le=100)
    cadence_minutes: int = Field(default=1440, ge=60, le=43200)
    enabled: bool = True
    run_workflow: bool = True
    allow_provider_credits: bool = False
    next_run_at: datetime | None = None


class DiscoveryPolicyUpdateRequest(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    provider_key: str | None = Field(default=None, pattern=SOURCE_PATTERN)
    query: str | None = Field(default=None, max_length=500)
    countries: list[str] | None = Field(default=None, max_length=25)
    industries: list[str] | None = Field(default=None, max_length=25)
    keywords: list[str] | None = Field(default=None, max_length=25)
    headcounts: list[str] | None = Field(default=None, max_length=8)
    max_new_per_run: int | None = Field(default=None, ge=1, le=100)
    cadence_minutes: int | None = Field(default=None, ge=60, le=43200)
    enabled: bool | None = None
    run_workflow: bool | None = None
    allow_provider_credits: bool | None = None
    next_run_at: datetime | None = None


def register_discovery_routes(
    app: FastAPI,
    prefix: str,
    tenant_dependency: Callable[..., TenantContext],
    session_dependency: Callable[..., Session],
    provider_registry_getter: Callable[[], ProviderRegistry],
) -> None:
    if not hasattr(app.state, "discovery_provider"):
        app.state.discovery_provider = HunterDiscoveryProvider()
    if not hasattr(app.state, "extra_discovery_providers"):
        app.state.extra_discovery_providers = {AI_SOURCE_KEY: build_ai_discovery_provider()}

    def discovery_service(
        context: TenantContext = Depends(tenant_dependency),
        db: Session = Depends(session_dependency),
    ) -> CompanyDiscoveryService:
        return CompanyDiscoveryService(
            db,
            context,
            app.state.discovery_provider,
            provider_registry_getter(),
            extra_providers=app.state.extra_discovery_providers,
        )

    base = prefix + "/company-discovery"

    @app.get(base + "/suggest")
    def suggest(product_id: str, svc: CompanyDiscoveryService = Depends(discovery_service)):
        return svc.suggest_criteria(product_id)

    @app.get(base + "/sources")
    def sources(svc: CompanyDiscoveryService = Depends(discovery_service)):
        return svc.sources()

    @app.post(base + "/preview")
    def preview(
        body: DiscoveryPreviewRequest,
        svc: CompanyDiscoveryService = Depends(discovery_service),
    ):
        criteria = normalize_criteria(**body.model_dump(exclude={"provider_key"}))
        try:
            return svc.preview(criteria, provider_key=body.provider_key)
        except ProviderError as error:
            # Provider messages are fixed, credential-free strings; the code drives UI wording.
            category = getattr(error, "category", "PROVIDER_ERROR")
            conflict = category in {"NOT_CONFIGURED", "DAILY_LIMIT", "INSUFFICIENT_BALANCE"}
            detail = PROVIDER_MESSAGES.get(category, str(error))
            return JSONResponse(
                {"detail": detail, "code": category}, status_code=409 if conflict else 502
            )

    @app.post(base + "/add", status_code=201)
    def add(body: DiscoveryAddRequest, svc: CompanyDiscoveryService = Depends(discovery_service)):
        return svc.add([item.model_dump() for item in body.companies])

    @app.get(base + "/policies")
    def policies(svc: CompanyDiscoveryService = Depends(discovery_service)):
        return [_policy_output(item) for item in svc.list()]

    @app.post(base + "/policies", status_code=201)
    def create_policy(
        body: DiscoveryPolicyCreateRequest,
        svc: CompanyDiscoveryService = Depends(discovery_service),
    ):
        return _policy_output(svc.create(body.model_dump()))

    @app.get(base + "/policies/{policy_id}")
    def get_policy(policy_id: str, svc: CompanyDiscoveryService = Depends(discovery_service)):
        return _policy_output(svc.get(policy_id))

    @app.patch(base + "/policies/{policy_id}")
    def update_policy(
        policy_id: str,
        body: DiscoveryPolicyUpdateRequest,
        svc: CompanyDiscoveryService = Depends(discovery_service),
    ):
        return _policy_output(svc.update(policy_id, body.model_dump(exclude_unset=True)))

    @app.get(base + "/policies/{policy_id}/runs")
    def policy_runs(policy_id: str, svc: CompanyDiscoveryService = Depends(discovery_service)):
        return [_run_output(item) for item in svc.runs(policy_id)]

    @app.post(base + "/policies/{policy_id}/run", status_code=201)
    def run_policy(policy_id: str, svc: CompanyDiscoveryService = Depends(discovery_service)):
        return _run_output(svc.run_now(policy_id))


def _policy_output(item) -> dict[str, Any]:
    return {
        "id": item.id,
        "organization_id": item.organization_id,
        "product_id": item.product_id,
        "name": item.name,
        "provider_key": item.provider_key,
        "query": item.query,
        "countries": item.countries or [],
        "industries": item.industries or [],
        "keywords": item.keywords or [],
        "headcounts": item.headcounts or [],
        "max_new_per_run": item.max_new_per_run,
        "cadence_minutes": item.cadence_minutes,
        "enabled": item.enabled,
        "run_workflow": item.run_workflow,
        "allow_provider_credits": item.allow_provider_credits,
        "next_offset": item.next_offset,
        "next_run_at": item.next_run_at,
        "last_run_at": item.last_run_at,
        "last_status": item.last_status,
        "last_error_code": item.last_error_code,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _run_output(item) -> dict[str, Any]:
    return {
        "id": item.id,
        "policy_id": item.policy_id,
        "scheduled_for": item.scheduled_for,
        "started_at": item.started_at,
        "completed_at": item.completed_at,
        "status": item.status,
        "candidates_found": item.candidates_found,
        "companies_added": item.companies_added,
        "already_known": item.already_known,
        "workflows_completed": item.workflows_completed,
        "workflows_failed": item.workflows_failed,
        "added_relationship_ids": item.added_relationship_ids or [],
        "notes": item.notes or [],
        "error_code": item.error_code,
    }
