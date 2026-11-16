"""Language resolution and fixed phrases (no web stack needed)."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from marketingiq.application.follow_ups import compose_follow_up
from marketingiq.application.languages import (
    LANGUAGES,
    draft_language,
    pack,
    resolve_language,
)
from marketingiq.infrastructure.outbound import unsubscribe_footer


@pytest.mark.parametrize(
    ("requested", "country", "expected"),
    [
        (None, "SA", "ar"),
        ("auto", "eg", "ar"),
        ("auto", "FR", "fr"),
        ("auto", "US", "en"),
        ("auto", None, "en"),
        ("en", "SA", "en"),
        ("AR", "US", "ar"),
    ],
)
def test_resolve_language(requested, country, expected):
    assert resolve_language(requested, country) == expected


def test_unsupported_language_is_rejected():
    with pytest.raises(ValueError):
        resolve_language("xx", "SA")


def test_ai_only_languages_use_english_fixed_phrases():
    assert pack("fr") is LANGUAGES["en"]
    assert draft_language({"language": "ar"}) == "ar"
    assert draft_language({"language": "zz"}) == "en"
    assert draft_language(None) == "en"


def test_every_pack_formats_cleanly():
    for phrases in LANGUAGES.values():
        phrases.greeting_named.format(first_name="Sara")
        phrases.relevance_with_title.format(job_title="CIO", company="Acme", product="CardIQ")
        phrases.relevance.format(company="Acme", product="CardIQ")
        phrases.subject.format(product="CardIQ", company="Acme")
        for line in (*phrases.follow_up_1, *phrases.follow_up_2):
            assert "{" not in line.format(product="CardIQ")
        phrases.quote_header.format(date="2026-11-16")


def test_arabic_follow_up_and_footer():
    subject, body = compose_follow_up(
        2,
        first_name="سارة",
        product=SimpleNamespace(name="CardIQ"),
        original_subject="CardIQ لـ نيوم",
        original_body="مرحباً سارة،\n\nنص الرسالة.",
        original_sent_at=datetime(2026, 11, 1, 9, tzinfo=UTC),
        language="ar",
    )
    assert subject == "Re: CardIQ لـ نيوم"
    assert body.startswith("مرحباً سارة،")
    assert "CardIQ" in body and "آخر رسالة" in body
    assert "بتاريخ 2026-11-01 كتبت:" in body
    assert "> نص الرسالة." in body

    footer = unsubscribe_footer("نص", "https://x.test/u?t=1", "ar")
    assert "إلغاء الاشتراك" in footer and footer.endswith("https://x.test/u?t=1\n")
    assert "unsubscribe here" in unsubscribe_footer("Body", "https://x.test/u", "fr")
