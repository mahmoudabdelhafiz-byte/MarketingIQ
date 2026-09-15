from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from marketingiq.application.campaigns import CampaignDraftService
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.campaigns import CampaignChannel


class CampaignDraftRequest(BaseModel):
    qualification_id: str = Field(min_length=1, max_length=36)
    contact_id: str = Field(min_length=1, max_length=36)
    channel: CampaignChannel = CampaignChannel.EMAIL
    call_to_action: str | None = Field(default=None, max_length=500)


def register_campaign_routes(
    app: FastAPI,
    prefix: str,
    tenant_dependency: Callable[..., TenantContext],
    session_dependency: Callable[..., Session],
) -> None:
    def campaign_service(
        context: Annotated[TenantContext, Depends(tenant_dependency)],
        db: Annotated[Session, Depends(session_dependency)],
    ) -> CampaignDraftService:
        return CampaignDraftService(db, context)

    campaign_prefix = prefix + "/companies/{relationship_id}/campaign-drafts"

    @app.post(campaign_prefix, status_code=201)
    def generate_campaign_draft(
        relationship_id: str,
        body: CampaignDraftRequest,
        svc: Annotated[CampaignDraftService, Depends(campaign_service)],
    ):
        return _campaign_draft_output(
            svc.generate(
                relationship_id,
                body.qualification_id,
                body.contact_id,
                body.channel,
                body.call_to_action,
            )
        )

    @app.get(campaign_prefix)
    def campaign_drafts(
        relationship_id: str,
        svc: Annotated[CampaignDraftService, Depends(campaign_service)],
    ):
        return [_campaign_draft_output(item) for item in svc.list(relationship_id)]

    @app.get(campaign_prefix + "/{draft_id}")
    def campaign_draft(
        relationship_id: str,
        draft_id: str,
        svc: Annotated[CampaignDraftService, Depends(campaign_service)],
    ):
        return _campaign_draft_output(svc.get(relationship_id, draft_id))


def _campaign_draft_output(item) -> dict[str, Any]:
    return {
        "id": item.id,
        "organization_id": item.organization_id,
        "organization_company_id": item.organization_company_id,
        "company_id": item.company_id,
        "product_id": item.product_id,
        "qualification_id": item.qualification_id,
        "contact_id": item.contact_id,
        "channel": item.channel,
        "status": item.status,
        "subject": item.subject,
        "body": item.body,
        "call_to_action": item.call_to_action,
        "message_angle": item.message_angle,
        "personalization_snapshot": item.personalization_snapshot,
        "evidence_snapshot": item.evidence_snapshot,
        "workflow_version": item.workflow_version,
        "created_by_user_id": item.created_by_user_id,
        "created_at": item.created_at,
        "classification": item.classification,
        "redistribution_status": item.redistribution_status,
    }
