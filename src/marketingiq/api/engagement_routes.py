from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from marketingiq.application.engagement import OutreachEngagementService
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.engagement import EngagementEventType


class EngagementRecordRequest(BaseModel):
    event_type: EngagementEventType
    event_key: str = Field(min_length=8, max_length=100)
    reason_code: str | None = Field(default=None, max_length=100)
    occurred_at: datetime | None = None


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
        event = svc.record(
            relationship_id,
            attempt_id,
            event_type=body.event_type,
            event_key=body.event_key,
            reason_code=body.reason_code,
            occurred_at=body.occurred_at,
        )
        if event.draft_id != draft_id:
            raise ValueError("send attempt does not belong to this campaign draft")
        return _event_output(event)

    @app.get(base)
    def engagement_history(
        relationship_id: str,
        draft_id: str,
        attempt_id: str,
        svc: EngagementService,
    ):
        events = svc.list(relationship_id, attempt_id)
        if events and events[0].draft_id != draft_id:
            raise ValueError("send attempt does not belong to this campaign draft")
        return [_event_output(item) for item in events]

    @app.get(base + "/summary")
    def engagement_summary(
        relationship_id: str,
        draft_id: str,
        attempt_id: str,
        svc: EngagementService,
    ):
        events = svc.list(relationship_id, attempt_id)
        if events and events[0].draft_id != draft_id:
            raise ValueError("send attempt does not belong to this campaign draft")
        return svc.summary(relationship_id, attempt_id)


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
        "reason_code": item.reason_code,
        "occurred_at": item.occurred_at,
        "recorded_by_user_id": item.recorded_by_user_id,
        "created_at": item.created_at,
    }
