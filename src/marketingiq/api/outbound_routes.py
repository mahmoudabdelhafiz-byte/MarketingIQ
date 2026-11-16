from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from marketingiq.api.engagement_routes import register_engagement_routes
from marketingiq.application.follow_ups import FollowUpService
from marketingiq.application.outbound import OutboundSenderRegistry, OutboundSendService
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.follow_ups import FollowUpStatus
from marketingiq.domain.outbound import OutboundSendStatus, SuppressionSource
from marketingiq.infrastructure.outbound import SMTPEmailSender


class OutboundSendRequest(BaseModel):
    provider: str = Field(default="SMTP", min_length=1, max_length=50)
    idempotency_key: str = Field(min_length=8, max_length=100)
    follow_ups: bool = False
    follow_up_auto_send: bool = False


class FollowUpScheduleRequest(BaseModel):
    auto_send: bool = False
    delays_days: list[int] = Field(default_factory=lambda: [3, 7], min_length=1, max_length=2)


class FollowUpEditRequest(BaseModel):
    subject: str = Field(min_length=1, max_length=255)
    body: str = Field(min_length=1, max_length=20000)


class FollowUpSkipRequest(BaseModel):
    cancel_sequence: bool = False


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
        return OutboundSendService(
            db,
            context,
            app.state.outbound_sender_registry,
            unsubscribe_links=getattr(app.state, "unsubscribe_links", None),
        )

    OutboundService = Annotated[OutboundSendService, Depends(outbound_service)]

    def follow_up_service(
        context: Annotated[TenantContext, Depends(tenant_dependency)],
        db: Annotated[Session, Depends(session_dependency)],
    ) -> FollowUpService:
        return FollowUpService(
            db,
            context,
            app.state.outbound_sender_registry,
            unsubscribe_links=getattr(app.state, "unsubscribe_links", None),
        )

    FollowUps = Annotated[FollowUpService, Depends(follow_up_service)]
    draft_prefix = prefix + "/companies/{relationship_id}/campaign-drafts/{draft_id}"

    @app.post(draft_prefix + "/send", status_code=201)
    def send_campaign_draft(
        relationship_id: str,
        draft_id: str,
        body: OutboundSendRequest,
        svc: OutboundService,
        follow_ups: FollowUps,
    ):
        attempt = svc.send(
            relationship_id,
            draft_id,
            provider_key=body.provider,
            idempotency_key=body.idempotency_key,
        )
        output = _attempt_output(attempt)
        output["follow_ups"] = []
        if body.follow_ups and attempt.status == OutboundSendStatus.SENT:
            follow_ups.schedule(relationship_id, attempt.id, auto_send=body.follow_up_auto_send)
            output["follow_ups"] = follow_ups.for_attempt(relationship_id, attempt.id)
        return output

    @app.post(draft_prefix + "/send-attempts/{attempt_id}/follow-ups", status_code=201)
    def schedule_follow_ups(
        relationship_id: str,
        draft_id: str,
        attempt_id: str,
        body: FollowUpScheduleRequest,
        follow_ups: FollowUps,
    ):
        follow_ups.schedule(
            relationship_id,
            attempt_id,
            auto_send=body.auto_send,
            delays_days=tuple(body.delays_days),
        )
        return follow_ups.for_attempt(relationship_id, attempt_id)

    @app.get(draft_prefix + "/send-attempts/{attempt_id}/follow-ups")
    def attempt_follow_ups(
        relationship_id: str, draft_id: str, attempt_id: str, follow_ups: FollowUps
    ):
        return follow_ups.for_attempt(relationship_id, attempt_id)

    @app.get(prefix + "/follow-ups")
    def list_follow_ups(follow_ups: FollowUps, status: FollowUpStatus | None = None):
        return follow_ups.list(statuses=(status,) if status else None)

    @app.patch(prefix + "/follow-ups/{follow_up_id}")
    def edit_follow_up(follow_up_id: str, body: FollowUpEditRequest, follow_ups: FollowUps):
        item = follow_ups.edit(follow_up_id, subject=body.subject, body=body.body)
        return follow_ups.describe(item)

    @app.post(prefix + "/follow-ups/{follow_up_id}/send")
    def send_follow_up(follow_up_id: str, follow_ups: FollowUps):
        return follow_ups.describe(follow_ups.send_now(follow_up_id))

    @app.post(prefix + "/follow-ups/{follow_up_id}/skip")
    def skip_follow_up(follow_up_id: str, body: FollowUpSkipRequest, follow_ups: FollowUps):
        return [
            follow_ups.describe(item)
            for item in follow_ups.skip(follow_up_id, cancel_sequence=body.cancel_sequence)
        ]

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

    register_engagement_routes(app, prefix, tenant_dependency, session_dependency)


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
