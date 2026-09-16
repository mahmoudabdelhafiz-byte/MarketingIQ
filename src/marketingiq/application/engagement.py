from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketingiq.application.authorization import Permission, require_permission
from marketingiq.application.errors import ConflictError, NotFoundError
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.campaigns import CampaignDraft
from marketingiq.domain.engagement import EngagementEventType, EngagementSource, OutreachEngagementEvent
from marketingiq.domain.models import AuditLog, ContactEmail, OrganizationCompany
from marketingiq.domain.outbound import OutboundSendAttempt, OutboundSendStatus, SuppressionEntry, SuppressionSource


class OutreachEngagementService:
    def __init__(self, session: Session, tenant: TenantContext, now: datetime | None = None) -> None:
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
    ) -> OutreachEngagementEvent:
        require_permission(self.tenant, Permission.RECORD_ENGAGEMENT)
        relationship = self._relationship(relationship_id)
        attempt = self._attempt(relationship, attempt_id)
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

        draft = self.session.scalar(
            select(CampaignDraft).where(
                CampaignDraft.id == attempt.draft_id,
                CampaignDraft.organization_id == self.tenant.organization_id,
            )
        )
        if draft is None:
            raise NotFoundError("Campaign draft not found")

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
            reason_code=reason_code.strip()[:100] if reason_code else None,
            metadata_json={"product_id": draft.product_id},
            occurred_at=occurred_at or self.now,
            recorded_by_user_id=self.tenant.actor_user_id,
            created_at=self.now,
        )
        self.session.add(event)
        self.session.flush()

        if event_type in {
            EngagementEventType.BOUNCED,
            EngagementEventType.COMPLAINT,
            EngagementEventType.OPT_OUT,
        }:
            self._suppress_recipient(attempt, event_type)

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

    def list(self, relationship_id: str, attempt_id: str) -> list[OutreachEngagementEvent]:
        require_permission(self.tenant, Permission.READ)
        relationship = self._relationship(relationship_id)
        attempt = self._attempt(relationship, attempt_id)
        return list(
            self.session.scalars(
                select(OutreachEngagementEvent)
                .where(
                    OutreachEngagementEvent.organization_id == self.tenant.organization_id,
                    OutreachEngagementEvent.organization_company_id == relationship.id,
                    OutreachEngagementEvent.send_attempt_id == attempt.id,
                )
                .order_by(OutreachEngagementEvent.occurred_at, OutreachEngagementEvent.id)
            )
        )

    def summary(self, relationship_id: str, attempt_id: str) -> dict:
        attempt = self._attempt(self._relationship(relationship_id), attempt_id)
        events = self.list(relationship_id, attempt_id)
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

    def _attempt(self, relationship: OrganizationCompany, attempt_id: str) -> OutboundSendAttempt:
        attempt = self.session.scalar(
            select(OutboundSendAttempt).where(
                OutboundSendAttempt.id == attempt_id,
                OutboundSendAttempt.organization_id == self.tenant.organization_id,
                OutboundSendAttempt.organization_company_id == relationship.id,
                OutboundSendAttempt.company_id == relationship.company_id,
            )
        )
        if attempt is None:
            raise NotFoundError("Outbound send attempt not found")
        return attempt

    def _suppress_recipient(
        self, attempt: OutboundSendAttempt, event_type: EngagementEventType
    ) -> None:
        email = self.session.get(ContactEmail, attempt.contact_email_id)
        if email is None:
            return
        normalized = email.email.strip().lower()
        existing = self.session.scalar(
            select(SuppressionEntry).where(
                SuppressionEntry.organization_id == self.tenant.organization_id,
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
        self.session.add(
            SuppressionEntry(
                organization_id=self.tenant.organization_id,
                email_normalized=normalized,
                source=source,
                reason=f"Automatically suppressed after {event_type.value}",
                created_by_user_id=self.tenant.actor_user_id,
                created_at=self.now,
            )
        )
