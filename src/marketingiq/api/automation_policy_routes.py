from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from marketingiq.application.automation_policies import AutomationPolicyService
from marketingiq.application.research import ProviderRegistry
from marketingiq.application.tenant import TenantContext


class AutomationPolicyCreateRequest(BaseModel):
    relationship_id: str = Field(min_length=1, max_length=36)
    product_id: str = Field(min_length=1, max_length=36)
    cadence_minutes: int = Field(ge=60, le=43200)
    enabled: bool = True
    allow_provider_credits: bool = False
    contact_provider: str = Field(default="HUNTER", min_length=1, max_length=100)
    max_contacts: int = Field(default=10, ge=1, le=50)
    max_steps: int = Field(default=4, ge=1, le=4)
    next_run_at: datetime | None = None


class AutomationPolicyUpdateRequest(BaseModel):
    enabled: bool | None = None
    cadence_minutes: int | None = Field(default=None, ge=60, le=43200)
    allow_provider_credits: bool | None = None
    contact_provider: str | None = Field(default=None, min_length=1, max_length=100)
    max_contacts: int | None = Field(default=None, ge=1, le=50)
    max_steps: int | None = Field(default=None, ge=1, le=4)
    next_run_at: datetime | None = None


class RunDuePoliciesRequest(BaseModel):
    limit: int = Field(default=20, ge=1, le=100)


def register_automation_policy_routes(
    app: FastAPI,
    prefix: str,
    tenant_dependency: Callable[..., TenantContext],
    session_dependency: Callable[..., Session],
    provider_registry_getter: Callable[[], ProviderRegistry],
) -> None:
    def policy_service(
        context: TenantContext = Depends(tenant_dependency),
        db: Session = Depends(session_dependency),
    ) -> AutomationPolicyService:
        return AutomationPolicyService(db, context, provider_registry_getter())

    base = prefix + "/automation-policies"

    @app.get(base)
    def list_policies(svc: AutomationPolicyService = Depends(policy_service)):
        return [_policy_output(item) for item in svc.list()]

    @app.post(base, status_code=201)
    def create_policy(
        body: AutomationPolicyCreateRequest,
        svc: AutomationPolicyService = Depends(policy_service),
    ):
        return _policy_output(svc.create(**body.model_dump()))

    @app.post(base + "/run-due")
    def run_due_policies(
        body: RunDuePoliciesRequest,
        svc: AutomationPolicyService = Depends(policy_service),
    ):
        return [_run_output(item) for item in svc.run_due(limit=body.limit)]

    @app.get(base + "/{policy_id}")
    def get_policy(
        policy_id: str,
        svc: AutomationPolicyService = Depends(policy_service),
    ):
        return _policy_output(svc.get(policy_id))

    @app.patch(base + "/{policy_id}")
    def update_policy(
        policy_id: str,
        body: AutomationPolicyUpdateRequest,
        svc: AutomationPolicyService = Depends(policy_service),
    ):
        changes = body.model_dump(exclude_unset=True)
        return _policy_output(svc.update(policy_id, changes))

    @app.get(base + "/{policy_id}/runs")
    def policy_runs(
        policy_id: str,
        svc: AutomationPolicyService = Depends(policy_service),
    ):
        return [_run_output(item) for item in svc.runs(policy_id)]


def _policy_output(item) -> dict:
    return {
        "id": item.id,
        "organization_id": item.organization_id,
        "organization_company_id": item.organization_company_id,
        "product_id": item.product_id,
        "enabled": item.enabled,
        "cadence_minutes": item.cadence_minutes,
        "allow_provider_credits": item.allow_provider_credits,
        "contact_provider": item.contact_provider,
        "max_contacts": item.max_contacts,
        "max_steps": item.max_steps,
        "next_run_at": item.next_run_at,
        "last_run_at": item.last_run_at,
        "last_status": item.last_status,
        "last_error_code": item.last_error_code,
        "run_as_user_id": item.run_as_user_id,
        "created_by_user_id": item.created_by_user_id,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _run_output(item) -> dict:
    return {
        "id": item.id,
        "organization_id": item.organization_id,
        "policy_id": item.policy_id,
        "scheduled_for": item.scheduled_for,
        "started_at": item.started_at,
        "completed_at": item.completed_at,
        "status": item.status,
        "stop_reason": item.stop_reason,
        "executed_steps": item.executed_steps,
        "error_code": item.error_code,
        "run_as_user_id": item.run_as_user_id,
        "triggered_by_user_id": item.triggered_by_user_id,
    }
