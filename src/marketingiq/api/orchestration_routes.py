from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from marketingiq.application.authorization import Permission, require_permission
from marketingiq.application.orchestration import (
    AutomationOrchestrationService,
    OrchestrationStep,
)
from marketingiq.application.research import ProviderRegistry
from marketingiq.application.tenant import TenantContext


class OrchestrationExecuteRequest(BaseModel):
    step: OrchestrationStep
    allow_provider_credits: bool = False
    contact_provider: str = "HUNTER"
    max_contacts: int = Field(default=10, ge=1, le=50)


class OrchestrationRunRequest(BaseModel):
    allow_provider_credits: bool = False
    contact_provider: str = "HUNTER"
    max_contacts: int = Field(default=10, ge=1, le=50)
    max_steps: int = Field(default=4, ge=1, le=4)


def register_orchestration_routes(
    app: FastAPI,
    prefix: str,
    tenant_dependency: Callable[..., TenantContext],
    session_dependency: Callable[..., Session],
    provider_registry_getter: Callable[[], ProviderRegistry],
) -> None:
    def orchestration_service(
        context: Annotated[TenantContext, Depends(tenant_dependency)],
        db: Annotated[Session, Depends(session_dependency)],
    ) -> AutomationOrchestrationService:
        return AutomationOrchestrationService(db, context, provider_registry_getter())

    Service = Annotated[AutomationOrchestrationService, Depends(orchestration_service)]
    base = prefix + "/companies/{relationship_id}/automation"

    @app.get(base + "/plan")
    def plan(
        svc: Service,
        relationship_id: str,
        product_id: str,
    ):
        return svc.plan(relationship_id, product_id)

    @app.post(base + "/execute")
    def execute(
        svc: Service,
        relationship_id: str,
        product_id: str,
        body: OrchestrationExecuteRequest,
    ):
        return svc.execute_step(
            relationship_id,
            product_id,
            body.step,
            allow_provider_credits=body.allow_provider_credits,
            contact_provider=body.contact_provider,
            max_contacts=body.max_contacts,
        )

    @app.post(base + "/run")
    def run(
        svc: Service,
        relationship_id: str,
        product_id: str,
        body: OrchestrationRunRequest,
    ):
        require_permission(svc.tenant, Permission.RUN_PUBLIC_RESEARCH)
        return svc.run_until_gate(
            relationship_id,
            product_id,
            allow_provider_credits=body.allow_provider_credits,
            contact_provider=body.contact_provider,
            max_contacts=body.max_contacts,
            max_steps=body.max_steps,
        )
