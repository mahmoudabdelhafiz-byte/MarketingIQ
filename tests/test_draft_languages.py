"""Drafts written in the recipient's language (deterministic template and AI)."""

import pytest
from test_ai_campaigns import FakeAIProvider, _service
from test_campaigns import seed_campaign, service_for

from marketingiq.application.errors import ConflictError
from marketingiq.domain.campaign_ai import CampaignAIResult


def _ids(seeded):
    return seeded["relationship"].id, seeded["qualification"].id, seeded["contact"].id


def _set_country(session, seeded, code):
    seeded["company"].country_code = code
    session.flush()


def test_template_draft_is_arabic_for_saudi_company(session):
    seeded = seed_campaign(session)
    _set_country(session, seeded, "SA")
    draft = service_for(session, seeded).generate(*_ids(seeded))

    assert draft.personalization_snapshot["language"] == "ar"
    assert draft.body.startswith("مرحباً Ava،")
    assert draft.subject == "MarketingIQ لـ Acme Logistics"
    assert "مع خالص التحية" in draft.body
    assert draft.call_to_action.startswith("هل يناسبكم")


def test_explicit_language_overrides_country(session):
    seeded = seed_campaign(session)
    _set_country(session, seeded, "SA")
    draft = service_for(session, seeded).generate(*_ids(seeded), language="en")
    assert draft.personalization_snapshot["language"] == "en"
    assert "Hi Ava," in draft.body


def test_default_stays_english_without_country(session):
    seeded = seed_campaign(session)
    draft = service_for(session, seeded).generate(*_ids(seeded))
    assert draft.personalization_snapshot["language"] == "en"


def test_unsupported_language_conflict(session):
    seeded = seed_campaign(session)
    with pytest.raises(ConflictError, match="LANGUAGE_UNSUPPORTED"):
        service_for(session, seeded).generate(*_ids(seeded), language="xx")


class ArabicProvider(FakeAIProvider):
    def generate(self, request):
        self.requests.append(request)
        return CampaignAIResult(
            subject="فكرة لـ Acme Logistics",
            body="مرحباً Ava،\n\nلاحظت أن Acme Logistics تعمل في قطاع الخدمات اللوجستية.",
            used_fact_keys=("industry",),
            model=self.model,
        )


def test_ai_draft_requests_and_keeps_arabic(session):
    seeded = seed_campaign(session)
    _set_country(session, seeded, "AE")
    provider = ArabicProvider()
    draft = _service(session, seeded, provider).generate(*_ids(seeded))

    request = provider.requests[0]
    assert (request.language, request.language_name) == ("ar", "Arabic")
    assert request.call_to_action.startswith("هل يناسبكم")
    assert draft.personalization_snapshot["generation_mode"] == "AI_ASSISTED"
    assert draft.personalization_snapshot["language"] == "ar"
    assert draft.subject == "فكرة لـ Acme Logistics"


def test_ai_reply_in_wrong_language_falls_back_to_arabic_template(session):
    seeded = seed_campaign(session)
    _set_country(session, seeded, "SA")
    draft = _service(session, seeded, FakeAIProvider()).generate(*_ids(seeded))  # English body
    assert draft.personalization_snapshot["language"] == "ar"
    assert draft.body.startswith("مرحباً Ava،")


def test_ai_only_language_falls_back_to_english_template(session):
    seeded = seed_campaign(session)
    provider = FakeAIProvider(fail="TIMEOUT")
    draft = _service(session, seeded, provider).generate(*_ids(seeded), language="fr")
    assert provider.requests[0].language_name == "French"
    assert draft.personalization_snapshot["language"] == "en"
    assert draft.personalization_snapshot["requested_language"] == "fr"
    assert "Hi Ava," in draft.body
