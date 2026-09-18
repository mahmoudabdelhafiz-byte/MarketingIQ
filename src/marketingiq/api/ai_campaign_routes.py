from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from marketingiq.application.ai_campaigns import GroundedAICampaignService
from marketingiq.application.campaigns import CampaignDraftService
from marketingiq.application.tenant import TenantContext
from marketingiq.infrastructure.campaign_ai_provider import OpenAICampaignAIProvider


class AICampaignDraftRequest(BaseModel):
    qualification_id: str = Field(min_length=1, max_length=36)
    contact_id: str = Field(min_length=1, max_length=36)
    call_to_action: str | None = Field(default=None, max_length=500)
    fallback_to_deterministic: bool = True


def register_ai_campaign_routes(
    app: FastAPI,
    prefix: str,
    tenant_dependency: Callable[..., TenantContext],
    session_dependency: Callable[..., Session],
) -> None:
    def ai_service(
        context: TenantContext = Depends(tenant_dependency),
        db: Session = Depends(session_dependency),
    ) -> GroundedAICampaignService:
        return GroundedAICampaignService(db, context, OpenAICampaignAIProvider())

    base = prefix + "/companies/{relationship_id}/campaign-drafts"

    @app.post(base + "/ai", status_code=201)
    def generate_ai_campaign_draft(
        relationship_id: str,
        body: AICampaignDraftRequest,
        svc: GroundedAICampaignService = Depends(ai_service),
    ):
        draft = svc.generate(
            relationship_id,
            body.qualification_id,
            body.contact_id,
            call_to_action=body.call_to_action,
            fallback_to_deterministic=body.fallback_to_deterministic,
        )
        return _campaign_draft_output(draft, svc.base)

    @app.get(prefix + "/campaign-ai/status")
    def campaign_ai_status(context: TenantContext = Depends(tenant_dependency)):
        provider = OpenAICampaignAIProvider()
        return provider.status()


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
