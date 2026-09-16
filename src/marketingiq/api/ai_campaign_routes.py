from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from marketingiq.api.campaign_routes import _campaign_draft_output
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
