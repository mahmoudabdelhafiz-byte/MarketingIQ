from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from marketingiq.application.pipeline import SalesPipelineService
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.pipeline import OpportunityStage


class OpportunityCreateRequest(BaseModel):
    send_attempt_id: str = Field(min_length=1, max_length=36)
    estimated_value: float | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    next_action: str | None = Field(default=None, max_length=500)


class OpportunityStageRequest(BaseModel):
    stage: OpportunityStage
    note: str | None = Field(default=None, max_length=2000)
    reason_code: str | None = Field(default=None, max_length=100)
    occurred_at: datetime | None = None


class OpportunityUpdateRequest(BaseModel):
    estimated_value: float | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    next_action: str | None = Field(default=None, max_length=500)


def register_pipeline_routes(
    app: FastAPI,
    prefix: str,
    tenant_dependency: Callable[..., TenantContext],
    session_dependency: Callable[..., Session],
) -> None:
    def pipeline_service(
        context: Annotated[TenantContext, Depends(tenant_dependency)],
        db: Annotated[Session, Depends(session_dependency)],
    ) -> SalesPipelineService:
        return SalesPipelineService(db, context)

    PipelineService = Annotated[SalesPipelineService, Depends(pipeline_service)]
    base = prefix + "/companies/{relationship_id}/opportunities"

    @app.post(base, status_code=201)
    def create_opportunity(
        relationship_id: str,
        body: OpportunityCreateRequest,
        svc: PipelineService,
    ):
        return _opportunity_output(
            svc.create(
                relationship_id,
                body.send_attempt_id,
                estimated_value=body.estimated_value,
                currency=body.currency,
                next_action=body.next_action,
            )
        )

    @app.get(base)
    def opportunities(relationship_id: str, svc: PipelineService):
        return [_opportunity_output(item) for item in svc.list(relationship_id)]

    @app.get(base + "/{opportunity_id}")
    def opportunity(
        relationship_id: str,
        opportunity_id: str,
        svc: PipelineService,
    ):
        return _opportunity_output(svc.get(relationship_id, opportunity_id))

    @app.patch(base + "/{opportunity_id}")
    def update_opportunity(
        relationship_id: str,
        opportunity_id: str,
        body: OpportunityUpdateRequest,
        svc: PipelineService,
    ):
        return _opportunity_output(
            svc.update_details(
                relationship_id,
                opportunity_id,
                estimated_value=body.estimated_value,
                currency=body.currency,
                next_action=body.next_action,
            )
        )

    @app.post(base + "/{opportunity_id}/stage")
    def move_opportunity_stage(
        relationship_id: str,
        opportunity_id: str,
        body: OpportunityStageRequest,
        svc: PipelineService,
    ):
        return _opportunity_output(
            svc.move_stage(
                relationship_id,
                opportunity_id,
                body.stage,
                note=body.note,
                reason_code=body.reason_code,
                occurred_at=body.occurred_at,
            )
        )

    @app.get(base + "/{opportunity_id}/history")
    def opportunity_history(
        relationship_id: str,
        opportunity_id: str,
        svc: PipelineService,
    ):
        return [
            _stage_event_output(item)
            for item in svc.history(relationship_id, opportunity_id)
        ]


def _opportunity_output(item) -> dict[str, Any]:
    estimated_value = None
    if item.estimated_value is not None:
        estimated_value = float(item.estimated_value)
    return {
        "id": item.id,
        "organization_id": item.organization_id,
        "organization_company_id": item.organization_company_id,
        "company_id": item.company_id,
        "product_id": item.product_id,
        "qualification_id": item.qualification_id,
        "contact_id": item.contact_id,
        "draft_id": item.draft_id,
        "send_attempt_id": item.send_attempt_id,
        "stage": item.stage,
        "estimated_value": estimated_value,
        "currency": item.currency,
        "next_action": item.next_action,
        "owner_user_id": item.owner_user_id,
        "created_by_user_id": item.created_by_user_id,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _stage_event_output(item) -> dict[str, Any]:
    return {
        "id": item.id,
        "opportunity_id": item.opportunity_id,
        "sequence_number": item.sequence_number,
        "from_stage": item.from_stage,
        "to_stage": item.to_stage,
        "note": item.note,
        "reason_code": item.reason_code,
        "occurred_at": item.occurred_at,
        "created_by_user_id": item.created_by_user_id,
        "created_at": item.created_at,
    }
