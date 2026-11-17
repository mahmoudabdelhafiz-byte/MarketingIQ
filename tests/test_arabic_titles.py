"""Arabic job titles are matched to personas (real titles from a Saudi company website)."""

import pytest
from sqlalchemy import select
from test_contacts import seed_case
from test_decision_makers import HOME, FakePublic, make_service, setup_personas

from marketingiq.application.decision_makers import name_key
from marketingiq.application.role_resolver import (
    PersonaSpec,
    PersonCandidate,
    normalize_text,
    resolve_by_rules,
    translate_arabic_title,
)
from marketingiq.domain.decision_makers import RoleMatch
from marketingiq.domain.models import ContactCandidate

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


ARABIC_LEADERSHIP = """<html><body><h1>القيادة</h1>
<div class="member"><h3>الأستاذ عبد العزيز الكثيري</h3>
<p>الرئيس التنفيذي – عضو اللجنة التنفيذية لشركة مهارة</p></div>
<div class="member"><h3>الأستاذ علي العتيبي</h3><p>نائب الرئيس للمبيعات والعمليات</p></div>
</body></html>"""


def test_name_key_handles_arabic_names_and_honorifics():
    assert name_key("الأستاذ عبد العزيز الكثيري") == "عبد العزيز الكثيري"
    assert name_key("عبد العزيز الكثيري") == name_key("الاستاذ عبدُ العزيز الكثيري")
    assert name_key("Dr. Sara Ahmed") == "sara ahmed"
    assert name_key("") == ""


def test_refreshing_an_arabic_leadership_page_keeps_one_record_per_person(session):
    case = seed_case(session)
    setup_personas(session, case)
    public = FakePublic(pages={"/": HOME, "/about-us/leadership": ARABIC_LEADERSHIP})
    rel = case["relationship"].id

    first = make_service(session, case, public=public).discover(rel)
    make_service(session, case, public=public).discover(rel, force_refresh=True)
    make_service(session, case, public=public).discover(rel, force_refresh=True)

    people = list(session.scalars(select(ContactCandidate)))
    assert len(people) == 2
    ceo = next(c for c in first["contacts"] if "الكثيري" in (c["full_name"] or ""))
    assert ceo["persona_name"] == "Executive leadership"
    assert ceo["role_match"] == "HIGH"


def test_existing_duplicates_are_marked_excluded_not_deleted(session):
    case = seed_case(session)
    setup_personas(session, case)
    public = FakePublic(pages={"/": HOME, "/about-us/leadership": ARABIC_LEADERSHIP})
    rel = case["relationship"].id
    make_service(session, case, public=public).discover(rel)
    original = session.scalars(select(ContactCandidate)).first()
    # A copy saved by the earlier, Latin-only duplicate check.
    copy = ContactCandidate(
        **{
            c.name: getattr(original, c.name)
            for c in ContactCandidate.__table__.columns
            if c.name not in {"id", "created_at", "updated_at", "provider_contact_reference"}
        }
    )
    session.add(copy)
    session.flush()

    make_service(session, case, public=public).discover(rel, force_refresh=True)

    session.refresh(copy)
    session.refresh(original)
    assert copy.review_status == "EXCLUDED" and copy.review_note.startswith("Duplicate of")
    assert original.review_status != "EXCLUDED"
    assert session.get(ContactCandidate, copy.id) is not None
