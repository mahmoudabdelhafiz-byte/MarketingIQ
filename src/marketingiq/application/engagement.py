from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketingiq.application.authorization import Permission, require_permission
from marketingiq.application.errors import ConflictError, NotFoundError
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.campaigns import CampaignDraft
from marketingiq.domain.engagement import (
    EngagementEventType,
    EngagementSource,
    OutreachEngagementEvent,
)
from marketingiq.domain.models import AuditLog, ContactEmail, OrganizationCompany
from marketingiq.domain.outbound import (
    OutboundSendAttempt,
    OutboundSendStatus,
    SuppressionEntry,
    SuppressionSource,
)

PROVIDER_INGESTIBLE_EVENTS = {
    EngagementEventType.DELIVERED,
    EngagementEventType.BOUNCED,
    EngagementEventType.COMPLAINT,
    EngagementEventType.REPLIED,
    EngagementEventType.OPT_OUT,
}


class OutreachEngagementService:
    def __init__(
        self,
        session: Session,
        tenant: TenantContext,
        now: datetime | None = None,
    ) -> None:
        self.session = session
        self.tenant = tenant
        self.now = now or datetime.now(UTC)

    def record(
        self,
        relationship_id: str,
        attempt_id: str,
        *,
        event_type: EngagementEventType,
        event_key: str,
        source: EngagementSource = EngagementSource.MANUAL,
        reason_code: str | None = None,
        occurred_at: datetime | None = None,
        draft_id: str | None = None,
    ) -> OutreachEngagementEvent:
        require_permission(self.tenant, Permission.RECORD_ENGAGEMENT)
        relationship = self._relationship(relationship_id)
        attempt = self._attempt(relationship, attempt_id, draft_id)
        if attempt.status != OutboundSendStatus.SENT:
            raise ConflictError("ENGAGEMENT_REQUIRES_SENT_ATTEMPT")

        clean_key = event_key.strip()
        if len(clean_key) < 8 or len(clean_key) > 100:
            raise ValueError("event_key must contain 8 to 100 characters")
        existing = self.session.scalar(
            select(OutreachEngagementEvent).where(
                OutreachEngagementEvent.organization_id == self.tenant.organization_id,
                OutreachEngagementEvent.event_key == clean_key,
            )
        )
        if existing is not None:
            if existing.send_attempt_id != attempt.id or existing.event_type != event_type:
                raise ConflictError("ENGAGEMENT_EVENT_KEY_REUSED")
            return existing

        draft = self._draft(attempt)
        event = OutreachEngagementEvent(
            organization_id=self.tenant.organization_id,
            organization_company_id=relationship.id,
            company_id=relationship.company_id,
            send_attempt_id=attempt.id,
            draft_id=attempt.draft_id,
            contact_id=attempt.contact_id,
            contact_email_id=attempt.contact_email_id,
            event_key=clean_key,
            event_type=event_type,
            source=source,
            provider_key=None,
            provider_event_id=None,
            reason_code=_clean_reason_code(reason_code),
            metadata_json={"product_id": draft.product_id},
            occurred_at=_event_time(occurred_at, self.now),
            recorded_by_user_id=self.tenant.actor_user_id,
            created_at=self.now,
        )
        self.session.add(event)
        self.session.flush()

        if event_type in _SUPPRESSION_EVENTS:
            _suppress_recipient(
                self.session,
                self.tenant.organization_id,
                attempt,
                event_type,
                actor_user_id=self.tenant.actor_user_id,
                now=self.now,
            )

        self.session.add(
            AuditLog(
                organization_id=self.tenant.organization_id,
                actor_user_id=self.tenant.actor_user_id,
                action="outreach_engagement.recorded",
                entity_type="outreach_engagement_event",
                entity_id=event.id,
                metadata_json={
                    "company_id": relationship.company_id,
                    "send_attempt_id": attempt.id,
                    "draft_id": attempt.draft_id,
                    "contact_id": attempt.contact_id,
                    "event_type": event_type.value,
                    "source": source.value,
                    "product_id": draft.product_id,
                },
            )
        )
        return event

    def list(
        self,
        relationship_id: str,
        attempt_id: str,
        *,
        draft_id: str | None = None,
    ) -> list[OutreachEngagementEvent]:
        require_permission(self.tenant, Permission.READ)
        relationship = self._relationship(relationship_id)
        attempt = self._attempt(relationship, attempt_id, draft_id)
        return list(
            self.session.scalars(
                select(OutreachEngagementEvent)
                .where(
                    OutreachEngagementEvent.organization_id == self.tenant.organization_id,
                    OutreachEngagementEvent.organization_company_id == relationship.id,
                    OutreachEngagementEvent.send_attempt_id == attempt.id,
                )
                .order_by(
                    OutreachEngagementEvent.occurred_at,
                    OutreachEngagementEvent.id,
                )
            )
        )

    def summary(
        self,
        relationship_id: str,
        attempt_id: str,
        *,
        draft_id: str | None = None,
    ) -> dict:
        attempt = self._attempt(self._relationship(relationship_id), attempt_id, draft_id)
        events = self.list(relationship_id, attempt_id, draft_id=draft_id)
        types = [item.event_type for item in events]
        delivery_status = "SENT"
        if EngagementEventType.BOUNCED in types:
            delivery_status = "BOUNCED"
        elif EngagementEventType.DELIVERED in types:
            delivery_status = "DELIVERED"

        sentiment = "UNKNOWN"
        if EngagementEventType.POSITIVE_REPLY in types:
            sentiment = "POSITIVE"
        elif EngagementEventType.NEGATIVE_REPLY in types:
            sentiment = "NEGATIVE"
        elif EngagementEventType.REPLIED in types:
            sentiment = "NEUTRAL_OR_UNKNOWN"

        return {
            "send_attempt_id": attempt.id,
            "delivery_status": delivery_status,
            "replied": any(
                value in types
                for value in {
                    EngagementEventType.REPLIED,
                    EngagementEventType.POSITIVE_REPLY,
                    EngagementEventType.NEGATIVE_REPLY,
                }
            ),
            "reply_sentiment": sentiment,
            "complaint": EngagementEventType.COMPLAINT in types,
            "opted_out": EngagementEventType.OPT_OUT in types,
            "event_count": len(events),
            "latest_event_at": events[-1].occurred_at if events else None,
        }

    def _relationship(self, relationship_id: str) -> OrganizationCompany:
        item = self.session.scalar(
            select(OrganizationCompany).where(
                OrganizationCompany.id == relationship_id,
                OrganizationCompany.organization_id == self.tenant.organization_id,
            )
        )
        if item is None:
            raise NotFoundError("Company relationship not found")
        return item

    def _attempt(
        self,
        relationship: OrganizationCompany,
        attempt_id: str,
        draft_id: str | None = None,
    ) -> OutboundSendAttempt:
        conditions = [
            OutboundSendAttempt.id == attempt_id,
            OutboundSendAttempt.organization_id == self.tenant.organization_id,
            OutboundSendAttempt.organization_company_id == relationship.id,
            OutboundSendAttempt.company_id == relationship.company_id,
        ]
        if draft_id is not None:
            conditions.append(OutboundSendAttempt.draft_id == draft_id)
        attempt = self.session.scalar(select(OutboundSendAttempt).where(*conditions))
        if attempt is None:
            raise NotFoundError("Outbound send attempt not found")
        return attempt

    def _draft(self, attempt: OutboundSendAttempt) -> CampaignDraft:
        draft = self.session.scalar(
            select(CampaignDraft).where(
                CampaignDraft.id == attempt.draft_id,
                CampaignDraft.organization_id == self.tenant.organization_id,
            )
        )
        if draft is None:
            raise NotFoundError("Campaign draft not found")
        return draft


class ProviderEngagementIngestionService:
    """Record normalized, authenticated provider outcomes without impersonating a tenant user."""

    def __init__(self, session: Session, now: datetime | None = None) -> None:
        self.session = session
        self.now = now or datetime.now(UTC)

    def ingest(
        self,
        organization_id: str,
        provider_key: str,
        attempt_id: str,
        *,
        provider_message_id: str,
        provider_event_id: str,
        event_type: EngagementEventType,
        occurred_at: datetime,
        reason_code: str | None = None,
    ) -> OutreachEngagementEvent:
        clean_provider = _provider_key(provider_key)
        clean_message_id = provider_message_id.strip()
        clean_event_id = provider_event_id.strip()
        if not clean_message_id or len(clean_message_id) > 255:
            raise ValueError("provider_message_id must contain 1 to 255 characters")
        if len(clean_event_id) < 8 or len(clean_event_id) > 150:
            raise ValueError("provider_event_id must contain 8 to 150 characters")
        if event_type not in PROVIDER_INGESTIBLE_EVENTS:
            raise ConflictError("ENGAGEMENT_PROVIDER_EVENT_TYPE_UNSUPPORTED")

        attempt = self.session.scalar(
            select(OutboundSendAttempt).where(
                OutboundSendAttempt.id == attempt_id,
                OutboundSendAttempt.organization_id == organization_id,
                OutboundSendAttempt.provider_key == clean_provider,
                OutboundSendAttempt.provider_message_id == clean_message_id,
            )
        )
        if attempt is None:
            raise NotFoundError("Outbound send attempt not found for provider event")
        if attempt.status != OutboundSendStatus.SENT:
            raise ConflictError("ENGAGEMENT_REQUIRES_SENT_ATTEMPT")

        existing = self.session.scalar(
            select(OutreachEngagementEvent).where(
                OutreachEngagementEvent.organization_id == organization_id,
                OutreachEngagementEvent.provider_key == clean_provider,
                OutreachEngagementEvent.provider_event_id == clean_event_id,
            )
        )
        if existing is not None:
            if existing.send_attempt_id != attempt.id or existing.event_type != event_type:
                raise ConflictError("ENGAGEMENT_PROVIDER_EVENT_REUSED")
            return existing

        relationship = self.session.scalar(
            select(OrganizationCompany).where(
                OrganizationCompany.id == attempt.organization_company_id,
                OrganizationCompany.organization_id == organization_id,
                OrganizationCompany.company_id == attempt.company_id,
            )
        )
        if relationship is None:
            raise NotFoundError("Company relationship not found")
        draft = self.session.scalar(
            select(CampaignDraft).where(
                CampaignDraft.id == attempt.draft_id,
                CampaignDraft.organization_id == organization_id,
            )
        )
        if draft is None:
            raise NotFoundError("Campaign draft not found")

        event = OutreachEngagementEvent(
            organization_id=organization_id,
            organization_company_id=relationship.id,
            company_id=relationship.company_id,
            send_attempt_id=attempt.id,
            draft_id=attempt.draft_id,
            contact_id=attempt.contact_id,
            contact_email_id=attempt.contact_email_id,
            event_key=_provider_event_key(clean_provider, clean_event_id),
            event_type=event_type,
            source=EngagementSource.PROVIDER,
            provider_key=clean_provider,
            provider_event_id=clean_event_id,
            reason_code=_clean_reason_code(reason_code),
            metadata_json={"product_id": draft.product_id},
            occurred_at=_event_time(occurred_at, self.now),
            recorded_by_user_id=None,
            created_at=self.now,
        )
        self.session.add(event)
        self.session.flush()

        if event_type in _SUPPRESSION_EVENTS:
            _suppress_recipient(
                self.session,
                organization_id,
                attempt,
                event_type,
                actor_user_id=None,
                now=self.now,
            )

        self.session.add(
            AuditLog(
                organization_id=organization_id,
                actor_user_id=None,
                action="outreach_engagement.provider_ingested",
                entity_type="outreach_engagement_event",
                entity_id=event.id,
                metadata_json={
                    "company_id": relationship.company_id,
                    "send_attempt_id": attempt.id,
                    "draft_id": attempt.draft_id,
                    "contact_id": attempt.contact_id,
                    "event_type": event_type.value,
                    "source": EngagementSource.PROVIDER.value,
                    "provider_key": clean_provider,
                    "product_id": draft.product_id,
                },
            )
        )
        return event


_SUPPRESSION_EVENTS = {
    EngagementEventType.BOUNCED,
    EngagementEventType.COMPLAINT,
    EngagementEventType.OPT_OUT,
}


def _suppress_recipient(
    session: Session,
    organization_id: str,
    attempt: OutboundSendAttempt,
    event_type: EngagementEventType,
    *,
    actor_user_id: str | None,
    now: datetime,
) -> None:
    email = session.get(ContactEmail, attempt.contact_email_id)
    if email is None:
        return
    normalized = email.email.strip().lower()
    existing = session.scalar(
        select(SuppressionEntry).where(
            SuppressionEntry.organization_id == organization_id,
            SuppressionEntry.email_normalized == normalized,
        )
    )
    if existing is not None:
        return
    source = {
        EngagementEventType.BOUNCED: SuppressionSource.BOUNCE,
        EngagementEventType.COMPLAINT: SuppressionSource.COMPLAINT,
        EngagementEventType.OPT_OUT: SuppressionSource.OPT_OUT,
    }[event_type]
    session.add(
        SuppressionEntry(
            organization_id=organization_id,
            email_normalized=normalized,
            source=source,
            reason=f"Automatically suppressed after {event_type.value}",
            created_by_user_id=actor_user_id,
            created_at=now,
        )
    )


def _provider_key(value: str) -> str:
    clean = value.strip().upper()
    if not clean or len(clean) > 50:
        raise ValueError("provider_key must contain 1 to 50 characters")
    if any(not (char.isalnum() or char in {"_", "-"}) for char in clean):
        raise ValueError("provider_key contains unsupported characters")
    return clean


def _provider_event_key(provider_key: str, provider_event_id: str) -> str:
    digest = hashlib.sha256(provider_event_id.encode("utf-8")).hexdigest()[:48]
    return f"provider:{provider_key.lower()}:{digest}"[:100]


def _clean_reason_code(value: str | None) -> str | None:
    return value.strip()[:100] if value else None


def _event_time(value: datetime | None, fallback: datetime) -> datetime:
    result = value or fallback
    if result.tzinfo is None:
        result = result.replace(tzinfo=UTC)
    return result
