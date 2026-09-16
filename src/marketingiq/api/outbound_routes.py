from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from marketingiq.application.outbound import OutboundSenderRegistry, OutboundSendService
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.outbound import SuppressionSource
from marketingiq.infrastructure.outbound import SMTPEmailSender


class OutboundSendRequest(BaseModel):
    provider: str = Field(default="SMTP", min_length=1, max_length=50)
    idempotency_key: str = Field(min_length=8, max_length=100)


class SuppressionRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    reason: str | None = Field(default=None, max_length=500)
    source: SuppressionSource = SuppressionSource.MANUAL


def register_outbound_routes(
    app: FastAPI,
    prefix: str,
    tenant_dependency: Callable[..., TenantContext],
    session_dependency: Callable[..., Session],
) -> None:
    if not hasattr(app.state, "outbound_sender_registry"):
        app.state.outbound_sender_registry = OutboundSenderRegistry([SMTPEmailSender()])

    def outbound_service(
        context: Annotated[TenantContext, Depends(tenant_dependency)],
        db: Annotated[Session, Depends(session_dependency)],
    ) -> OutboundSendService:
        return OutboundSendService(db, context, app.state.outbound_sender_registry)

    OutboundService = Annotated[OutboundSendService, Depends(outbound_service)]
    draft_prefix = prefix + "/companies/{relationship_id}/campaign-drafts/{draft_id}"

    @app.post(draft_prefix + "/send", status_code=201)
    def send_campaign_draft(
        relationship_id: str,
        draft_id: str,
        body: OutboundSendRequest,
        svc: OutboundService,
    ):
        return _attempt_output(
            svc.send(
                relationship_id,
                draft_id,
                provider_key=body.provider,
                idempotency_key=body.idempotency_key,
            )
        )

    @app.get(draft_prefix + "/send-attempts")
    def campaign_send_attempts(
        relationship_id: str,
        draft_id: str,
        svc: OutboundService,
    ):
        return [_attempt_output(item) for item in svc.list_attempts(relationship_id, draft_id)]

    @app.post(prefix + "/outbound/suppressions", status_code=201)
    def create_suppression(body: SuppressionRequest, svc: OutboundService):
        return _suppression_output(
            svc.suppress(body.email, reason=body.reason, source=body.source)
        )

    @app.get(prefix + "/outbound/suppressions")
    def suppressions(svc: OutboundService):
        return [_suppression_output(item) for item in svc.list_suppressions()]

    @app.get(prefix + "/outbound/providers/status")
    def outbound_provider_status(svc: OutboundService):
        return svc.provider_status()


def _attempt_output(item) -> dict[str, Any]:
    return {
        "id": item.id,
        "organization_id": item.organization_id,
        "organization_company_id": item.organization_company_id,
        "company_id": item.company_id,
        "draft_id": item.draft_id,
        "review_event_id": item.review_event_id,
        "contact_id": item.contact_id,
        "contact_email_id": item.contact_email_id,
        "provider_key": item.provider_key,
        "idempotency_key": item.idempotency_key,
        "status": item.status,
        "provider_message_id": item.provider_message_id,
        "error_category": item.error_category,
        "requested_by_user_id": item.requested_by_user_id,
        "requested_at": item.requested_at,
        "completed_at": item.completed_at,
    }


def _suppression_output(item) -> dict[str, Any]:
    return {
        "id": item.id,
        "organization_id": item.organization_id,
        "email": item.email_normalized,
        "source": item.source,
        "reason": item.reason,
        "created_by_user_id": item.created_by_user_id,
        "created_at": item.created_at,
    }
