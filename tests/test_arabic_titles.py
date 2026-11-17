"""Arabic job titles are matched to personas (real titles from a Saudi company website)."""

import pytest

from marketingiq.application.role_resolver import (
    PersonaSpec,
    PersonCandidate,
    normalize_text,
    resolve_by_rules,
    translate_arabic_title,
)
from marketingiq.domain.decision_makers import RoleMatch

PERSONAS = (
    PersonaSpec(
        id="exec",
        name="Executive leadership",
        titles=("CEO", "Managing Director", "General Manager", "Founder"),
        departments=("Executive", "Management"),
        seniorities=("C-level",),
    ),
    PersonaSpec(
        id="hr",
        name="HR leadership",
        titles=("CHRO", "HR Director", "Head of HR"),
        departments=("HR", "People"),
    ),
    PersonaSpec(
        id="it",
        name="IT leadership",
        titles=("CIO", "CTO", "IT Director", "Head of IT"),
        departments=("IT", "Technology"),
    ),
)


def resolve(title):
    return resolve_by_rules(PersonCandidate(key="x", title=title), PERSONAS)


@pytest.mark.parametrize(
    ("title", "persona", "match"),
    [
        ("الرئيس التنفيذي – عضو اللجنة التنفيذية لشركة مهارة", "exec", RoleMatch.HIGH),
        ("الرئيس التنفيذي", "exec", RoleMatch.HIGH),
        ("المدير العام", "exec", RoleMatch.HIGH),
        ("الرئيس التنفيذي للموارد البشرية", "hr", RoleMatch.HIGH),
        ("مدير الموارد البشرية", "hr", RoleMatch.MEDIUM),
        ("الرئيس التنفيذي للمعلومات", "it", RoleMatch.HIGH),
        ("مدير تقنية المعلومات", "it", RoleMatch.MEDIUM),
    ],
)
def test_arabic_titles_match_their_persona(title, persona, match):
    result = resolve(title)
    assert result.persona is not None and result.persona.id == persona
    # HIGH: an exact role; MEDIUM: the right function at manager level (as for English titles).
    assert result.role_match == match


def test_adviser_to_the_ceo_is_not_the_ceo():
    result = resolve(
        "مستشار الرئيس التنفيذي - نائب الرئيس التنفيذي للخدمات المشتركة – شركة مهارة"
    )
    assert result.role_match != RoleMatch.HIGH or result.persona.id != "exec"


def test_evp_is_not_read_as_ceo():
    assert translate_arabic_title("نائب الرئيس التنفيذي").startswith("executive vice president")
    assert "chief executive officer" not in translate_arabic_title("نائب الرئيس التنفيذي")


def test_prefixed_and_diacritic_forms():
    assert "sales" in translate_arabic_title("نائب الرئيس للمبيعات والعمليات")
    assert "operations" in translate_arabic_title("نائب الرئيس للمبيعات والعمليات")
    assert translate_arabic_title("المُدير العامّ") == "general manager"
    assert normalize_text("مدير إدارة الموارد البشرية") == "director of human resources"


def test_english_titles_are_unchanged():
    assert translate_arabic_title("Chief Executive Officer") == "Chief Executive Officer"
    assert resolve("CEO").persona.id == "exec"
