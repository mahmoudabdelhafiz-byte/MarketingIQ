from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from marketingiq.application.engagement import (
    OutreachEngagementService,
    ProviderEngagementIngestionService,
)
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.engagement import EngagementEventType
from marketingiq.infrastructure.engagement_webhooks import (
    EngagementWebhookAuthenticationError,
    EngagementWebhookNotConfigured,
    verify_engagement_webhook,
)


class EngagementRecordRequest(BaseModel):
    event_type: EngagementEventType
    event_key: str = Field(min_length=8, max_length=100)
    reason_code: str | None = Field(default=None, max_length=100)
    occurred_at: datetime | None = None


class ProviderEngagementWebhookRequest(BaseModel):
    send_attempt_id: str = Field(min_length=1, max_length=36)
    provider_message_id: str = Field(min_length=1, max_length=255)
    provider_event_id: str = Field(min_length=8, max_length=150)
    event_type: EngagementEventType
    reason_code: str | None = Field(default=None, max_length=100)
    occurred_at: datetime


def register_engagement_routes(
    app: FastAPI,
    prefix: str,
    tenant_dependency: Callable[..., TenantContext],
    session_dependency: Callable[..., Session],
) -> None:
    def engagement_service(
        context: Annotated[TenantContext, Depends(tenant_dependency)],
        db: Annotated[Session, Depends(session_dependency)],
    ) -> OutreachEngagementService:
        return OutreachEngagementService(db, context)

    EngagementService = Annotated[OutreachEngagementService, Depends(engagement_service)]
    base = (
        prefix
        + "/companies/{relationship_id}/campaign-drafts/{draft_id}"
        + "/send-attempts/{attempt_id}/engagements"
    )

    @app.post(base, status_code=201)
    def record_engagement(
        relationship_id: str,
        draft_id: str,
        attempt_id: str,
        body: EngagementRecordRequest,
        svc: EngagementService,
    ):
        return _event_output(
            svc.record(
                relationship_id,
                attempt_id,
                event_type=body.event_type,
                event_key=body.event_key,
                reason_code=body.reason_code,
                occurred_at=body.occurred_at,
                draft_id=draft_id,
            )
        )

    @app.get(base)
    def engagement_history(
        relationship_id: str,
        draft_id: str,
        attempt_id: str,
        svc: EngagementService,
    ):
        return [
            _event_output(item)
            for item in svc.list(relationship_id, attempt_id, draft_id=draft_id)
        ]

    @app.get(base + "/summary")
    def engagement_summary(
        relationship_id: str,
        draft_id: str,
        attempt_id: str,
        svc: EngagementService,
    ):
        return svc.summary(relationship_id, attempt_id, draft_id=draft_id)

    @app.post(prefix + "/webhooks/outbound/{provider_key}/engagement")
    async def provider_engagement_webhook(
        org_id: str,
        provider_key: str,
        request: Request,
        db: Annotated[Session, Depends(session_dependency)],
        webhook_timestamp: Annotated[
            str | None,
            Header(alias="X-MarketingIQ-Webhook-Timestamp"),
        ] = None,
        webhook_signature: Annotated[
            str | None,
            Header(alias="X-MarketingIQ-Webhook-Signature"),
        ] = None,
    ):
        raw_body = await request.body()
        try:
            verify_engagement_webhook(raw_body, webhook_timestamp, webhook_signature)
        except EngagementWebhookNotConfigured as error:
            raise HTTPException(status_code=503, detail=str(error)) from None
        except EngagementWebhookAuthenticationError as error:
            raise HTTPException(status_code=401, detail=str(error)) from None

        try:
            body = ProviderEngagementWebhookRequest.model_validate_json(raw_body)
        except ValidationError:
            raise HTTPException(status_code=422, detail="Invalid webhook payload") from None

        event = ProviderEngagementIngestionService(db).ingest(
            org_id,
            provider_key,
            body.send_attempt_id,
            provider_message_id=body.provider_message_id,
            provider_event_id=body.provider_event_id,
            event_type=body.event_type,
            reason_code=body.reason_code,
            occurred_at=body.occurred_at,
        )
        return _event_output(event)


def _event_output(item) -> dict[str, Any]:
    return {
        "id": item.id,
        "organization_id": item.organization_id,
        "organization_company_id": item.organization_company_id,
        "company_id": item.company_id,
        "send_attempt_id": item.send_attempt_id,
        "draft_id": item.draft_id,
        "contact_id": item.contact_id,
        "contact_email_id": item.contact_email_id,
        "event_key": item.event_key,
        "event_type": item.event_type,
        "source": item.source,
        "provider_key": item.provider_key,
        "provider_event_id": item.provider_event_id,
        "reason_code": item.reason_code,
        "occurred_at": item.occurred_at,
        "recorded_by_user_id": item.recorded_by_user_id,
        "created_at": item.created_at,
    }
