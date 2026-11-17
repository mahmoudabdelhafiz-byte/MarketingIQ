"""'Auto' language without a country on the record; AI fact lists that name prompt fields."""

from __future__ import annotations

import pytest

from marketingiq.application.languages import (
    country_code_from,
    country_from_website,
    resolve_language,
)


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("SA", "SA"),
        ("sa", "SA"),
        ("Saudi Arabia", "SA"),
        ("المملكة العربية السعودية", "SA"),
        ({"name": "United Arab Emirates"}, "AE"),
        ("Atlantis", None),
        (None, None),
    ],
)
def test_country_code_from_fact_values(value, code):
    assert country_code_from(value) == code


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("https://maharah.sa/about", "SA"),
        ("https://www.example.com.sa", "SA"),
        ("tasc.ae", "AE"),
        ("https://maharah.com", None),
        (None, None),
    ],
)
def test_country_from_website(url, code):
    assert country_from_website(url) == code
    if code == "SA":
        assert resolve_language("auto", code) == "ar"


def test_prompt_fields_listed_as_used_facts_do_not_reject_the_draft():
    from marketingiq.application.ai_campaigns import PROMPT_FIELDS, GroundedAICampaignService

    validate = GroundedAICampaignService._validate_ai_result

    class Base:
        @staticmethod
        def _validate_content(*_args, **_kwargs):
            return None

    class Fake:
        base = Base()

    Fact = type("Fact", (), {})
    fact = Fact()
    fact.fact_key = "industry"
    validate(Fake(), "Subject", "مرحباً", ("industry", "company_name"), [fact], "ar")
    assert "company_name" in PROMPT_FIELDS
    with pytest.raises(ValueError, match="unsupported grounding keys: revenue"):
        validate(Fake(), "Subject", "مرحباً", ("revenue",), [fact], "ar")
