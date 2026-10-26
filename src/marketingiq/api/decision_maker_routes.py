from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from marketingiq.application.decision_makers import DecisionMakerService
from marketingiq.application.research import ProviderRegistry
from marketingiq.application.tenant import TenantContext


class PersonaWrite(BaseModel):
    id: str | None = Field(default=None, max_length=36)
    name: str = Field(min_length=1, max_length=120)
    titles: list[str] = Field(default_factory=list, max_length=30)
    departments: list[str] = Field(default_factory=list, max_length=10)
    seniorities: list[str] = Field(default_factory=list, max_length=10)
    max_contacts: int = Field(default=2, ge=1, le=3)
    priority: int = Field(default=100, ge=1, le=1000)
    is_active: bool = True


class PersonasWrite(BaseModel):
    personas: list[PersonaWrite] = Field(default_factory=list, max_length=12)


class DecisionMakerDiscoverRequest(BaseModel):
    product_id: str | None = Field(default=None, max_length=36)
    allow_provider_credits: bool = False
    force_refresh: bool = False


class DecisionMakerReviewRequest(BaseModel):
    action: Literal["APPROVE", "EXCLUDE", "RESET"]
    note: str | None = Field(default=None, max_length=500)


def register_decision_maker_routes(
    app: FastAPI,
    prefix: str,
    tenant_dependency: Callable[..., TenantContext],
    session_dependency: Callable[..., Session],
    provider_registry_getter: Callable[[], ProviderRegistry],
) -> None:
    def service(
        context: TenantContext = Depends(tenant_dependency),
        db: Session = Depends(session_dependency),
    ) -> DecisionMakerService:
        factory = getattr(app.state, "decision_maker_factory", None)
        if factory is not None:
            return factory(db, context)
        return DecisionMakerService(db, context, provider_registry_getter())

    @app.get(prefix + "/products/{product_id}/personas")
    def personas(product_id: str, svc: DecisionMakerService = Depends(service)):
        return svc.list_personas(product_id)

    @app.put(prefix + "/products/{product_id}/personas")
    def replace_personas(
        product_id: str, body: PersonasWrite, svc: DecisionMakerService = Depends(service)
    ):
        return svc.replace_personas(product_id, [p.model_dump() for p in body.personas])

    base = prefix + "/companies/{relationship_id}/decision-makers"

    @app.get(base)
    def decision_makers(
        relationship_id: str,
        product_id: str | None = None,
        svc: DecisionMakerService = Depends(service),
    ):
        return svc.summary(relationship_id, product_id)

    @app.post(base + "/discover")
    def discover(
        relationship_id: str,
        body: DecisionMakerDiscoverRequest,
        svc: DecisionMakerService = Depends(service),
    ):
        return svc.discover(
            relationship_id,
            body.product_id,
            allow_provider_credits=body.allow_provider_credits,
            force_refresh=body.force_refresh,
        )

    @app.get(base + "/{contact_id}")
    def detail(relationship_id: str, contact_id: str, svc: DecisionMakerService = Depends(service)):
        return svc.detail(relationship_id, contact_id)

    @app.post(base + "/{contact_id}/review")
    def review(
        relationship_id: str,
        contact_id: str,
        body: DecisionMakerReviewRequest,
        svc: DecisionMakerService = Depends(service),
    ):
        return svc.review(relationship_id, contact_id, body.action, body.note)

    @app.post(base + "/{contact_id}/find-email")
    def find_email(
        relationship_id: str, contact_id: str, svc: DecisionMakerService = Depends(service)
    ):
        return svc.find_email(relationship_id, contact_id)

    @app.post(base + "/{contact_id}/verify-email")
    def verify_email(
        relationship_id: str, contact_id: str, svc: DecisionMakerService = Depends(service)
    ):
        return svc.verify_email(relationship_id, contact_id)
