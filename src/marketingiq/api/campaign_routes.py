from __future__ import annotations

from collections.abc import Callable
from typing import Any

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


class CampaignDraftEditRequest(BaseModel):
    subject: str | None = Field(default=None, max_length=255)
    body: str | None = Field(default=None, max_length=20000)
    call_to_action: str | None = Field(default=None, max_length=500)
    reason: str | None = Field(default=None, max_length=500)


class CampaignReviewRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class CampaignRejectRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


def register_campaign_routes(
    app: FastAPI,
    prefix: str,
    tenant_dependency: Callable[..., TenantContext],
    session_dependency: Callable[..., Session],
) -> None:
    def campaign_service(
        context: TenantContext = Depends(tenant_dependency),
        db: Session = Depends(session_dependency),
    ) -> CampaignDraftService:
        return CampaignDraftService(db, context)

    campaign_prefix = prefix + "/companies/{relationship_id}/campaign-drafts"

    @app.post(campaign_prefix, status_code=201)
    def generate_campaign_draft(
        relationship_id: str,
        body: CampaignDraftRequest,
        svc: CampaignDraftService = Depends(campaign_service),
    ):
        draft = svc.generate(
            relationship_id,
            body.qualification_id,
            body.contact_id,
            body.channel,
            body.call_to_action,
        )
        return _campaign_draft_output(draft, svc)

    @app.get(campaign_prefix)
    def campaign_drafts(
        relationship_id: str,
        svc: CampaignDraftService = Depends(campaign_service),
    ):
        return [_campaign_draft_output(item, svc) for item in svc.list(relationship_id)]

    @app.get(campaign_prefix + "/{draft_id}")
    def campaign_draft(
        relationship_id: str,
        draft_id: str,
        svc: CampaignDraftService = Depends(campaign_service),
    ):
        return _campaign_draft_output(svc.get(relationship_id, draft_id), svc)

    @app.patch(campaign_prefix + "/{draft_id}")
    def edit_campaign_draft(
        relationship_id: str,
        draft_id: str,
        body: CampaignDraftEditRequest,
        svc: CampaignDraftService = Depends(campaign_service),
    ):
        svc.edit(
            relationship_id,
            draft_id,
            subject=body.subject,
            body=body.body,
            call_to_action=body.call_to_action,
            reason=body.reason,
        )
        return _campaign_draft_output(svc.get(relationship_id, draft_id), svc)

    @app.post(campaign_prefix + "/{draft_id}/approve")
    def approve_campaign_draft(
        relationship_id: str,
        draft_id: str,
        body: CampaignReviewRequest,
        svc: CampaignDraftService = Depends(campaign_service),
    ):
        svc.approve(relationship_id, draft_id, body.reason)
        return _campaign_draft_output(svc.get(relationship_id, draft_id), svc)

    @app.post(campaign_prefix + "/{draft_id}/reject")
    def reject_campaign_draft(
        relationship_id: str,
        draft_id: str,
        body: CampaignRejectRequest,
        svc: CampaignDraftService = Depends(campaign_service),
    ):
        svc.reject(relationship_id, draft_id, body.reason)
        return _campaign_draft_output(svc.get(relationship_id, draft_id), svc)

    @app.get(campaign_prefix + "/{draft_id}/history")
    def campaign_draft_history(
        relationship_id: str,
        draft_id: str,
        svc: CampaignDraftService = Depends(campaign_service),
    ):
        return [
            _campaign_review_output(item) for item in svc.history(relationship_id, draft_id)
        ]


def _campaign_draft_output(item, svc: CampaignDraftService) -> dict[str, Any]:
    state = svc.effective_state_for(item)
    return {
        "id": item.id,
        "organization_id": item.organization_id,
        "organization_company_id": item.organization_company_id,
        "company_id": item.company_id,
        "product_id": item.product_id,
        "qualification_id": item.qualification_id,
        "contact_id": item.contact_id,
        "channel": item.channel,
        "status": state["status"],
        "subject": state["subject"],
        "body": state["body"],
        "call_to_action": state["call_to_action"],
        "revision_number": state["revision_number"],
        "last_review_action": state["last_action"],
        "last_review_at": state["last_action_at"],
        "generated_content": {
            "subject": item.subject,
            "body": item.body,
            "call_to_action": item.call_to_action,
        },
        "message_angle": item.message_angle,
        "personalization_snapshot": item.personalization_snapshot,
        "evidence_snapshot": item.evidence_snapshot,
        "workflow_version": item.workflow_version,
        "created_by_user_id": item.created_by_user_id,
        "created_at": item.created_at,
        "classification": item.classification,
        "redistribution_status": item.redistribution_status,
    }


def _campaign_review_output(item) -> dict[str, Any]:
    return {
        "id": item.id,
        "draft_id": item.draft_id,
        "revision_number": item.revision_number,
        "action": item.action,
        "status": item.status,
        "subject": item.subject,
        "body": item.body,
        "call_to_action": item.call_to_action,
        "reason": item.reason,
        "created_by_user_id": item.created_by_user_id,
        "created_at": item.created_at,
    }
