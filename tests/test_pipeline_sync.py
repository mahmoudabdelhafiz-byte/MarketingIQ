from __future__ import annotations

from sqlalchemy import select
from test_campaigns import seed_campaign
from test_engagement import engagement_service
from test_pipeline import pipeline_service, sent_attempt

from marketingiq.application.pipeline_sync import ScheduledPipelineSyncService
from marketingiq.domain.engagement import EngagementEventType
from marketingiq.domain.pipeline import (
    OpportunityStage,
    SalesOpportunity,
    SalesOpportunityStageEvent,
    StageEventSource,
)


def test_sync_creates_contacted_opportunity_once_for_sent_outreach(session):
    seeded = seed_campaign(session)
    _draft, attempt = sent_attempt(session, seeded)
    service = ScheduledPipelineSyncService(session)

    first = service.run()
    second = service.run()

    opportunity = session.scalar(
        select(SalesOpportunity).where(SalesOpportunity.send_attempt_id == attempt.id)
    )
    assert opportunity is not None
    assert opportunity.stage == OpportunityStage.CONTACTED
    assert opportunity.owner_user_id == attempt.requested_by_user_id
    assert first.created == 1
    assert first.processed == 1
    assert second.created == 0
    assert second.processed == 0

    history = list(
        session.scalars(
            select(SalesOpportunityStageEvent)
            .where(SalesOpportunityStageEvent.opportunity_id == opportunity.id)
            .order_by(SalesOpportunityStageEvent.sequence_number)
        )
    )
    assert len(history) == 1
    assert history[0].source == StageEventSource.SYSTEM
    assert history[0].created_by_user_id is None
    assert history[0].reason_code == "AUTO_CREATED_AFTER_SENT_OUTREACH"


def test_sync_promotes_contacted_to_responded_after_observed_reply(session):
    seeded = seed_campaign(session)
    _draft, attempt = sent_attempt(session, seeded)
    sync = ScheduledPipelineSyncService(session)
    sync.run()

    engagement_service(session, seeded).record(
        seeded["relationship"].id,
        attempt.id,
        event_type=EngagementEventType.REPLIED,
        event_key="pipeline-sync-reply-001",
    )
    result = sync.run()

    opportunity = session.scalar(
        select(SalesOpportunity).where(SalesOpportunity.send_attempt_id == attempt.id)
    )
    assert opportunity is not None
    assert opportunity.stage == OpportunityStage.RESPONDED
    assert result.responded == 1

    history = list(
        session.scalars(
            select(SalesOpportunityStageEvent)
            .where(SalesOpportunityStageEvent.opportunity_id == opportunity.id)
            .order_by(SalesOpportunityStageEvent.sequence_number)
        )
    )
    assert [item.to_stage for item in history] == [
        OpportunityStage.CONTACTED,
        OpportunityStage.RESPONDED,
    ]
    assert history[1].source == StageEventSource.SYSTEM
    assert history[1].created_by_user_id == seeded["user"].id
    assert history[1].reason_code == "AUTO_REPLY_DETECTED"

    repeated = sync.run()
    assert repeated.responded == 0
    assert repeated.processed == 0


def test_sync_does_not_overwrite_later_manual_pipeline_stage(session):
    seeded = seed_campaign(session)
    _draft, attempt = sent_attempt(session, seeded)
    sync = ScheduledPipelineSyncService(session)
    sync.run()

    opportunity = session.scalar(
        select(SalesOpportunity).where(SalesOpportunity.send_attempt_id == attempt.id)
    )
    assert opportunity is not None
    pipeline_service(session, seeded).move_stage(
        seeded["relationship"].id,
        opportunity.id,
        OpportunityStage.MEETING,
    )

    engagement_service(session, seeded).record(
        seeded["relationship"].id,
        attempt.id,
        event_type=EngagementEventType.POSITIVE_REPLY,
        event_key="pipeline-sync-late-reply-001",
    )
    result = sync.run()

    assert opportunity.stage == OpportunityStage.MEETING
    assert result.responded == 0
    history = list(
        session.scalars(
            select(SalesOpportunityStageEvent)
            .where(SalesOpportunityStageEvent.opportunity_id == opportunity.id)
            .order_by(SalesOpportunityStageEvent.sequence_number)
        )
    )
    assert [item.to_stage for item in history] == [
        OpportunityStage.CONTACTED,
        OpportunityStage.MEETING,
    ]
    assert history[1].source == StageEventSource.MANUAL


def test_sync_can_create_historical_opportunity_directly_as_responded(session):
    seeded = seed_campaign(session)
    _draft, attempt = sent_attempt(session, seeded)
    engagement_service(session, seeded).record(
        seeded["relationship"].id,
        attempt.id,
        event_type=EngagementEventType.REPLIED,
        event_key="pipeline-sync-historical-reply-001",
    )

    result = ScheduledPipelineSyncService(session).run()
    opportunity = session.scalar(
        select(SalesOpportunity).where(SalesOpportunity.send_attempt_id == attempt.id)
    )
    assert opportunity is not None
    assert opportunity.stage == OpportunityStage.RESPONDED
    assert result.created == 1
    assert result.responded == 1


def test_sync_batch_limit_validation(session):
    service = ScheduledPipelineSyncService(session)
    for value in (0, 501):
        try:
            service.run(limit=value)
        except ValueError as error:
            assert "between 1 and 500" in str(error)
        else:
            raise AssertionError("invalid pipeline sync batch limit was accepted")
