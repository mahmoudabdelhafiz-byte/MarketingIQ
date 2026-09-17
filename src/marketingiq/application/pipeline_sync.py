from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from marketingiq.application.pipeline import REPLY_EVENTS, SalesPipelineSyncService
from marketingiq.domain.engagement import OutreachEngagementEvent
from marketingiq.domain.outbound import OutboundSendAttempt, OutboundSendStatus
from marketingiq.domain.pipeline import OpportunityStage, SalesOpportunity

MAX_PIPELINE_SYNC_BATCH = 500


@dataclass(frozen=True)
class PipelineSyncResult:
    created: int
    responded: int
    processed: int


class ScheduledPipelineSyncService:
    """Synchronize sent outreach and observed replies without changing later sales stages."""

    def __init__(self, session: Session, now: datetime | None = None) -> None:
        self.session = session
        self.now = now or datetime.now(UTC)
        self.sync = SalesPipelineSyncService(session, now=self.now)

    def run(
        self,
        *,
        organization_id: str | None = None,
        limit: int = 100,
    ) -> PipelineSyncResult:
        if not 1 <= limit <= MAX_PIPELINE_SYNC_BATCH:
            raise ValueError("limit must be between 1 and 500")

        created = responded = processed = 0
        attempts = self._unsynced_attempts(organization_id, limit)
        for attempt in attempts:
            opportunity = self.sync.ensure_for_sent_attempt(attempt)
            processed += 1
            if opportunity is not None:
                created += 1
                if opportunity.stage == OpportunityStage.RESPONDED:
                    responded += 1

        remaining = limit - processed
        if remaining > 0:
            for opportunity in self._contacted_with_reply(organization_id, remaining):
                attempt = self.session.get(OutboundSendAttempt, opportunity.send_attempt_id)
                if attempt is None or attempt.status != OutboundSendStatus.SENT:
                    continue
                event = self.session.scalar(
                    select(OutreachEngagementEvent)
                    .where(
                        OutreachEngagementEvent.organization_id == opportunity.organization_id,
                        OutreachEngagementEvent.send_attempt_id == opportunity.send_attempt_id,
                        OutreachEngagementEvent.event_type.in_(REPLY_EVENTS),
                    )
                    .order_by(
                        OutreachEngagementEvent.occurred_at,
                        OutreachEngagementEvent.id,
                    )
                    .limit(1)
                )
                if event is None:
                    continue
                before = opportunity.stage
                synchronized = self.sync.synchronize_reply(
                    attempt,
                    occurred_at=event.occurred_at,
                    triggered_by_user_id=event.recorded_by_user_id,
                )
                processed += 1
                if (
                    synchronized is not None
                    and before == OpportunityStage.CONTACTED
                    and synchronized.stage == OpportunityStage.RESPONDED
                ):
                    responded += 1

        return PipelineSyncResult(created=created, responded=responded, processed=processed)

    def _unsynced_attempts(
        self,
        organization_id: str | None,
        limit: int,
    ) -> list[OutboundSendAttempt]:
        has_opportunity = exists(
            select(SalesOpportunity.id).where(
                SalesOpportunity.organization_id == OutboundSendAttempt.organization_id,
                SalesOpportunity.send_attempt_id == OutboundSendAttempt.id,
            )
        )
        query = select(OutboundSendAttempt).where(
            OutboundSendAttempt.status == OutboundSendStatus.SENT,
            ~has_opportunity,
        )
        if organization_id is not None:
            query = query.where(OutboundSendAttempt.organization_id == organization_id)
        query = query.order_by(
            OutboundSendAttempt.completed_at,
            OutboundSendAttempt.id,
        ).limit(limit)
        return list(self.session.scalars(query))

    def _contacted_with_reply(
        self,
        organization_id: str | None,
        limit: int,
    ) -> list[SalesOpportunity]:
        has_reply = exists(
            select(OutreachEngagementEvent.id).where(
                OutreachEngagementEvent.organization_id == SalesOpportunity.organization_id,
                OutreachEngagementEvent.send_attempt_id == SalesOpportunity.send_attempt_id,
                OutreachEngagementEvent.event_type.in_(REPLY_EVENTS),
            )
        )
        query = select(SalesOpportunity).where(
            SalesOpportunity.stage == OpportunityStage.CONTACTED,
            has_reply,
        )
        if organization_id is not None:
            query = query.where(SalesOpportunity.organization_id == organization_id)
        query = query.order_by(SalesOpportunity.updated_at, SalesOpportunity.id).limit(limit)
        return list(self.session.scalars(query))
