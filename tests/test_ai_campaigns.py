from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from test_campaigns import seed_campaign

from marketingiq.application.ai_campaigns import GroundedAICampaignService
from marketingiq.application.errors import ConflictError
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.campaign_ai import (
    CampaignAIGenerationAttempt,
    CampaignAIProviderError,
    CampaignAIResult,
)
from marketingiq.domain.models import AuditLog, MembershipRole

NOW = datetime(2026, 9, 17, 2, 0, tzinfo=UTC)


class FakeAIProvider:
    key = "FAKE_AI"
    configured = True
    model = "fake-grounded-model"

    def __init__(self, *, fail: str | None = None, used_fact_keys=("industry",)) -> None:
        self.fail = fail
        self.used_fact_keys = used_fact_keys
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        if self.fail:
            raise CampaignAIProviderError(self.fail)
        return CampaignAIResult(
            subject="A focused idea for Acme Logistics",
            body=(
                "Hi Ava,\n\n"
                "I noticed Acme Logistics operates in Logistics. "
                "MarketingIQ turns evidence into focused B2B pursuit decisions.\n\n"
                "Would a 20-minute conversation be useful to explore whether this "
                "could fit your priorities?\n\n"
                "Best regards,"
            ),
            used_fact_keys=tuple(self.used_fact_keys),
            model=self.model,
            input_tokens=120,
            output_tokens=80,
        )


def _service(session, seeded, provider):
    return GroundedAICampaignService(
        session,
        TenantContext(
            seeded["organization"].id,
            seeded["user"].id,
            MembershipRole.MARKETING_USER,
        ),
        provider,
        now=NOW,
    )


def test_ai_generation_uses_only_approved_public_fit_facts(session):
    seeded = seed_campaign(session)
    provider = FakeAIProvider()
    draft = _service(session, seeded, provider).generate(
        seeded["relationship"].id,
        seeded["qualification"].id,
        seeded["contact"].id,
    )

    assert draft.message_angle == "AI_GROUNDED_ROLE_ALIGNMENT"
    assert draft.personalization_snapshot["generation_mode"] == "AI_ASSISTED"
    assert draft.evidence_snapshot["grounding_fact_keys"] == ["industry"]
    assert draft.evidence_snapshot["used_fact_keys"] == ["industry"]
    assert len(provider.requests) == 1
    assert [fact.fact_key for fact in provider.requests[0].grounding_facts] == ["industry"]

    attempt = session.scalar(select(CampaignAIGenerationAttempt))
    assert attempt is not None
    assert attempt.status == "SUCCESS"
    assert attempt.draft_id == draft.id
    assert attempt.input_tokens == 120
    assert attempt.output_tokens == 80

    audit = session.scalar(select(AuditLog).where(AuditLog.action == "campaign_draft.ai_generated"))
    assert audit is not None
    metadata = str(audit.metadata_json)
    assert seeded["email"].email not in metadata
    assert "Hi Ava" not in metadata


def test_ai_generation_rejects_unsupported_fact_reference(session):
    seeded = seed_campaign(session)
    provider = FakeAIProvider(used_fact_keys=("industry", "budget"))

    with pytest.raises(ConflictError, match="CAMPAIGN_AI_GROUNDING_VALIDATION_FAILED"):
        _service(session, seeded, provider).generate(
            seeded["relationship"].id,
            seeded["qualification"].id,
            seeded["contact"].id,
            fallback_to_deterministic=False,
        )

    attempt = session.scalar(select(CampaignAIGenerationAttempt))
    assert attempt is not None
    assert attempt.status == "FAILED"
    assert attempt.error_category == "GROUNDING_VALIDATION_FAILED"
    assert attempt.draft_id is None


def test_ai_provider_failure_can_fall_back_without_claiming_ai_success(session):
    seeded = seed_campaign(session)
    provider = FakeAIProvider(fail="RATE_LIMITED")

    draft = _service(session, seeded, provider).generate(
        seeded["relationship"].id,
        seeded["qualification"].id,
        seeded["contact"].id,
        fallback_to_deterministic=True,
    )

    assert draft.workflow_version == "deterministic-campaign-draft-v1"
    attempt = session.scalar(select(CampaignAIGenerationAttempt))
    assert attempt is not None
    assert attempt.status == "FALLBACK"
    assert attempt.response_status == "DETERMINISTIC_FALLBACK"
    assert attempt.error_category == "RATE_LIMITED"
    assert attempt.fallback_used is True
    assert attempt.draft_id == draft.id
