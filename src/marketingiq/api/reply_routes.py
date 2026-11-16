"""Replies inbox: captured prospect replies, AI-suggested answers, outcomes."""

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from marketingiq.application.replies import ReplyAssistantService
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.replies import ReplyIntent, ReplyStatus
from marketingiq.infrastructure.reply_ai import build_reply_ai_provider


class ReplySendRequest(BaseModel):
    subject: str = Field(min_length=1, max_length=255)
    body: str = Field(min_length=1, max_length=20000)
    then_unsubscribe: bool = False


class ReplyIntentRequest(BaseModel):
    intent: ReplyIntent


class ReplyMeetingRequest(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


def register_reply_routes(
    app: FastAPI,
    prefix: str,
    tenant_dependency: Callable[..., TenantContext],
    session_dependency: Callable[..., Session],
) -> None:
    def reply_service(
        context: Annotated[TenantContext, Depends(tenant_dependency)],
        db: Annotated[Session, Depends(session_dependency)],
    ) -> ReplyAssistantService:
        factory = getattr(app.state, "reply_ai_factory", build_reply_ai_provider)
        return ReplyAssistantService(
            db,
            context,
            app.state.outbound_sender_registry,
            ai=factory(),
            unsubscribe_links=getattr(app.state, "unsubscribe_links", None),
        )

    Replies = Annotated[ReplyAssistantService, Depends(reply_service)]
    base = prefix + "/replies"

    @app.get(base)
    def list_replies(svc: Replies, status: ReplyStatus | None = None):
        return svc.list(status)

    @app.get(base + "/{reply_id}")
    def get_reply(reply_id: str, svc: Replies):
        return svc.describe(svc.get(reply_id))

    @app.post(base + "/{reply_id}/suggest")
    def suggest_reply(reply_id: str, svc: Replies):
        return svc.describe(svc.suggest(reply_id))

    @app.post(base + "/{reply_id}/intent")
    def set_reply_intent(reply_id: str, body: ReplyIntentRequest, svc: Replies):
        return svc.describe(svc.set_intent(reply_id, body.intent))

    @app.post(base + "/{reply_id}/send")
    def send_reply(reply_id: str, body: ReplySendRequest, svc: Replies):
        item = svc.send(
            reply_id,
            subject=body.subject,
            body=body.body,
            then_unsubscribe=body.then_unsubscribe,
        )
        return svc.describe(item)

    @app.post(base + "/{reply_id}/dismiss")
    def dismiss_reply(reply_id: str, svc: Replies):
        return svc.describe(svc.dismiss(reply_id))

    @app.post(base + "/{reply_id}/meeting-booked")
    def reply_meeting_booked(reply_id: str, body: ReplyMeetingRequest, svc: Replies):
        return svc.describe(svc.mark_meeting_booked(reply_id, body.note))

    @app.post(base + "/{reply_id}/not-interested")
    def reply_not_interested(reply_id: str, svc: Replies):
        return svc.describe(svc.mark_not_interested(reply_id))

    @app.post(base + "/{reply_id}/unsubscribe")
    def reply_unsubscribe(reply_id: str, svc: Replies):
        return svc.describe(svc.unsubscribe(reply_id))
