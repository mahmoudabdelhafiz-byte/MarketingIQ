"""Decision-maker discovery. Every provider (web, Hunter, OpenAI) is a local fake."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select
from test_contacts import NOW, seed_case

from marketingiq.application.campaigns import CampaignDraftService
from marketingiq.application.decision_maker_scoring import (
    EmailView,
    EvidenceView,
    email_state,
    employment_status,
    normalize_linkedin,
    suitability,
    titles_conflict,
)
from marketingiq.application.decision_makers import (
    DecisionMakerService,
    targeting_block_reason,
)
from marketingiq.application.errors import AuthorizationError, ConflictError, NotFoundError
from marketingiq.application.orchestration import AutomationOrchestrationService
from marketingiq.application.research import ProviderRegistry
from marketingiq.application.role_resolver import (
    PersonaSpec,
    PersonCandidate,
    RoleResolver,
    hunter_departments,
    hunter_seniorities,
    normalize_text,
    resolve_by_rules,
)
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.decision_makers import (
    ContactEvidence,
    ContactStatus,
    EmploymentStatus,
    EvidenceSourceType,
    RoleMatch,
    Suitability,
)
from marketingiq.domain.models import (
    AuditLog,
    ContactCandidate,
    ContactEmail,
    EmailVerificationStatus,
    MembershipRole,
    ProviderUsage,
    QualificationStatus,
)
from marketingiq.domain.providers import (
    ContactProviderResult,
    ProviderCapability,
    ProviderContact,
    ProviderContactSource,
    ProviderError,
)
from marketingiq.infrastructure.decision_maker_ai import (
    DecisionMakerAIError,
    OpenAIDecisionMakerClient,
)
from marketingiq.infrastructure.leadership_pages import LeadershipPageScanner, extract_people
from marketingiq.infrastructure.providers import HunterProvider

PERSONAS = [
    {
        "name": "Executive leadership",
        "titles": ["CEO", "Managing Director", "General Manager", "Founder"],
        "departments": ["Executive", "Management"],
        "seniorities": ["C-level"],
        "max_contacts": 1,
        "priority": 1,
    },
    {
        "name": "IT leadership",
        "titles": ["CIO", "CTO", "IT Director", "Head of IT"],
        "departments": ["IT", "Technology", "Digital"],
        "seniorities": ["C-level", "Director", "Head"],
        "max_contacts": 2,
        "priority": 2,
    },
    {
        "name": "HR leadership",
        "titles": ["CHRO", "HR Director", "Head of People", "Chief People Officer"],
        "departments": ["HR", "People"],
        "seniorities": ["C-level", "Director", "Head"],
        "max_contacts": 2,
        "priority": 3,
    },
]

HOME = """<html><body><nav><a href="/">Home</a><a href="/about-us/leadership">Leadership</a>
<a href="/contact">Contact</a></nav><h1>Welcome</h1></body></html>"""
LEADERSHIP = """<html><body><h1>Our leadership</h1>
<div class="member"><h3>Sara Ahmed</h3><p>Chief Information Officer</p>
<a href="https://www.linkedin.com/in/sara-ahmed-123/">LinkedIn</a></div>
<div class="member"><h3>Omar Khalid</h3><p>Chief Executive Officer</p></div>
<div class="member"><h3>Lina Haddad</h3><p>HR Director</p></div>
<div><h3>Our Clients</h3><p>Trusted by leaders</p></div>
<script>var x = "Fake Person"; </script>
</body></html>"""


class FakePublic:
    key = "PUBLIC_WEB"
    capabilities = frozenset({ProviderCapability.ENRICH_COMPANY})
    costs_credits = False
    configured = True

    def __init__(self, pages=None):
        self.pages = pages if pages is not None else {"/": HOME, "/about-us/leadership": LEADERSHIP}
        self.fetched = []
        self.redirects = {}

    def _fetch(self, url, allowed_hosts=None):
        self.fetched.append(url)
        target = self.redirects.get(url)
        if target:
            host = target.split("/")[2]
            if allowed_hosts is not None and host not in allowed_hosts:
                raise ProviderError("Redirect left the permitted site")
            url = target
        path = "/" + url.split("/", 3)[3] if url.count("/") >= 3 else "/"
        path = path if path == "/" else path.rstrip("/")
        if path not in self.pages:
            raise ProviderError("Public page returned HTTP 404")
        return url, self.pages[path], NOW


class FakeHunter:
    key = "HUNTER"
    capabilities = frozenset(
        {
            ProviderCapability.SEARCH_CONTACTS,
            ProviderCapability.FIND_EMAIL,
            ProviderCapability.VERIFY_EMAIL,
        }
    )
    costs_credits = True

    def __init__(self, contacts=(), found=None, verification="valid", configured=True):
        self.contacts = tuple(contacts)
        self.found = found
        self.verification = verification
        self._configured = configured
        self.calls = {"search": 0, "find": 0, "verify": 0}
        self.search_args = None

    @property
    def configured(self):
        return self._configured

    def search_decision_makers(self, domain, *, departments, seniorities, max_results):
        self.calls["search"] += 1
        self.search_args = {
            "domain": domain,
            "departments": departments,
            "seniorities": seniorities,
            "max_results": max_results,
        }
        return ContactProviderResult(self.key, self.contacts, "req-search", 1, 99)

    def search_contacts(self, domain, max_results):  # pragma: no cover - legacy path
        raise AssertionError("decision-maker discovery must use the filtered search")

    def find_email(self, person):
        self.calls["find"] += 1
        found = self.found(person) if callable(self.found) else self.found
        return ContactProviderResult(self.key, (found,) if found else (), "req-find", 1, 98)

    def verify_email(self, email):
        self.calls["verify"] += 1
        status = self.verification(email) if callable(self.verification) else self.verification
        return ContactProviderResult(
            self.key, (ProviderContact(reference=status, email=email, confidence=90),), "v", 1, 97
        )


class FakeAI:
    model = "test-model"

    def __init__(self, roles=None, people=None, error=None):
        self.roles = roles
        self.people = people
        self.error = error
        self.calls = {"roles": 0, "people": 0}
        self.last_candidates = None

    @property
    def configured(self):
        return True

    def resolve_roles(self, candidates, personas):
        self.calls["roles"] += 1
        self.last_candidates = candidates
        if self.error:
            raise self.error
        return self.roles(candidates) if callable(self.roles) else (self.roles or [])

    def extract_people(self, company_name, page_url, page_text):
        self.calls["people"] += 1
        if self.error:
            raise self.error
        return self.people or []


def hunter_contact(first, last, title, email=None, **extra):
    return ProviderContact(
        first_name=first,
        last_name=last,
        full_name=f"{first} {last}",
        job_title=title,
        position_raw=extra.pop("position_raw", title),
        department=extra.pop("department", None),
        seniority=extra.pop("seniority", None),
        email=email,
        confidence=extra.pop("confidence", 90),
        sources=extra.pop("sources", ()),
        verification_status=extra.pop("verification_status", None),
        verification_date=extra.pop("verification_date", None),
        linkedin=extra.pop("linkedin", None),
        **extra,
    )


def still_listed(uri="https://news.example.test/a"):
    return (
        ProviderContactSource(
            uri=uri, domain="news.example.test", last_seen_on="2026-08-20", still_on_page=True
        ),
    )


def make_service(session, case, *, public=None, hunter=None, ai=None, tenant=None):
    public = public if public is not None else FakePublic()
    providers = [public] + ([hunter] if hunter is not None else [])
    registry = ProviderRegistry(providers)
    return DecisionMakerService(
        session,
        tenant or case["tenant"],
        registry,
        ai_client=ai,
        page_scanner=LeadershipPageScanner(public, max_pages=4),
        now=NOW,
    )


def admin(case):
    return TenantContext(case["org"].id, case["user"].id, MembershipRole.ORGANIZATION_ADMIN)


def setup_personas(session, case, personas=PERSONAS):
    service = make_service(session, case, tenant=admin(case))
    service.replace_personas(case["qualification"].product_id, personas)
    return service


def by_name(summary, name):
    return next(c for c in summary["contacts"] if c["full_name"] == name)


# 1. Persona normalization ------------------------------------------------------------------
def test_persona_normalization_expands_abbreviations_and_maps_hunter_filters():
    assert normalize_text("Sr. VP, IT & Digital") == (
        "senior vice president information technology and digital"
    )
    assert normalize_text("CHRO") == "chief human resources officer"
    assert normalize_text("C-Level") == "c level"
    assert hunter_departments(["IT", "Technology", "Digital"]) == ["it"]
    assert hunter_departments(["HR", "People"]) == ["hr"]
    assert hunter_departments(["Communications", "PR"]) == ["communication"]
    assert hunter_seniorities(["C-level", "Director", "Head"]) == ["executive", "senior"]


def test_personas_are_product_config_and_fall_back_to_buyer_roles(session):
    case = seed_case(session, buyer_role="Chief Information Officer")
    service = make_service(session, case, tenant=admin(case))
    product_id = case["qualification"].product_id

    derived = service.list_personas(product_id)
    assert derived["derived_from_buyer_roles"] is True
    assert [p["name"] for p in derived["effective"]] == ["Chief Information Officer", "Procurement"]

    saved = service.replace_personas(
        product_id,
        [
            {"name": "  IT   leadership ", "titles": ["CIO", "cio", " CTO "], "max_contacts": 9},
        ],
    )
    assert saved["derived_from_buyer_roles"] is False
    persona = saved["personas"][0]
    assert persona["name"] == "IT leadership"
    assert persona["titles"] == ["CIO", "CTO"]
    assert persona["max_contacts"] == 3
    with pytest.raises(ConflictError, match="DUPLICATE"):
        service.replace_personas(product_id, [{"name": "A"}, {"name": "a"}])
    audit = session.scalar(select(AuditLog).where(AuditLog.action == "product.personas.updated"))
    assert audit.metadata_json["persona_names"] == ["IT leadership"]


# 2-4. Alias resolution -----------------------------------------------------------------------
SPECS = [
    PersonaSpec(
        str(i),
        p["name"],
        tuple(p["titles"]),
        tuple(p["departments"]),
        tuple(p["seniorities"]),
        p["max_contacts"],
        p["priority"],
    )
    for i, p in enumerate(PERSONAS)
]


def resolve(title, department=None):
    return resolve_by_rules(PersonCandidate("x", title, department), SPECS)


@pytest.mark.parametrize(
    "title",
    [
        "CIO",
        "CTO",
        "Chief Information Officer",
        "Director of IT",
        "Head of IT",
        "Group IT Director",
    ],
)
def test_it_aliases_resolve_to_it_persona(title):
    result = resolve(title)
    assert result.persona.name == "IT leadership"
    assert result.role_match == RoleMatch.HIGH


@pytest.mark.parametrize("title", ["CHRO", "HR Director", "Head of People", "Chief People Officer"])
def test_hr_aliases_resolve_to_hr_persona(title):
    result = resolve(title)
    assert result.persona.name == "HR leadership"
    assert result.role_match == RoleMatch.HIGH


@pytest.mark.parametrize("title", ["CEO", "MD", "GM", "Managing Director", "Founder & CEO"])
def test_exec_aliases_resolve_to_exec_persona(title):
    result = resolve(title)
    assert result.persona.name == "Executive leadership"
    assert result.role_match == RoleMatch.HIGH


def test_supporting_former_and_unrelated_titles_are_not_high_matches():
    assert resolve("Executive Assistant to the CEO").role_match == RoleMatch.LOW
    assert resolve("Former CIO").role_match == RoleMatch.NONE
    assert resolve("Deputy General Manager").role_match == RoleMatch.MEDIUM
    assert resolve("Software Engineer", "it").role_match == RoleMatch.LOW
    unrelated = resolve("Chief Financial Officer")
    assert unrelated.role_match == RoleMatch.NONE and unrelated.persona is None
    # Ambiguous acronyms are never guessed by the rules.
    cpo = resolve("CPO")
    assert cpo.role_match == RoleMatch.NONE and cpo.ambiguous is True


# 5. Hunter parsing ---------------------------------------------------------------------------
def test_hunter_decision_maker_search_sends_filters_and_parses_evidence_fields():
    seen = {}

    def handler(request):
        seen["params"] = dict(request.url.params)
        return httpx.Response(
            200,
            json={
                "data": {
                    "emails": [
                        {
                            "value": "sara@example.test",
                            "type": "personal",
                            "confidence": 94,
                            "first_name": "Sara",
                            "last_name": "Ahmed",
                            "position": "CIO",
                            "position_raw": "Chief Information Officer (Group)",
                            "seniority": "executive",
                            "department": "it",
                            "decision_maker": True,
                            "linkedin": "https://www.linkedin.com/in/sara-ahmed-123",
                            "sources": [
                                {
                                    "domain": "news.example.test",
                                    "uri": "https://news.example.test/a",
                                    "extracted_on": "2025-01-02",
                                    "last_seen_on": "2026-08-20",
                                    "still_on_page": True,
                                },
                                "not-a-dict",
                            ],
                            "verification": {"date": "2026-09-01", "status": "valid"},
                        }
                    ]
                },
                "meta": {"params": {"credits_used": 1}},
            },
        )

    provider = HunterProvider("key", httpx.Client(transport=httpx.MockTransport(handler)))
    result = provider.search_decision_makers(
        "example.test",
        departments=["it", "hr", "bogus"],
        seniorities=["executive", "senior", "wizard"],
        max_results=500,
    )
    assert seen["params"]["department"] == "hr,it"
    assert seen["params"]["seniority"] == "executive,senior"
    assert seen["params"]["type"] == "personal"
    assert seen["params"]["limit"] == "100"
    person = result.contacts[0]
    assert person.position_raw == "Chief Information Officer (Group)"
    assert person.decision_maker is True
    assert person.sources[0].still_on_page is True and len(person.sources) == 1
    assert person.verification_status == "valid"
    assert result.credits_used == 1


# 6. Credit-disabled behaviour ----------------------------------------------------------------
def test_credit_disabled_run_uses_only_public_sources(session):
    case = seed_case(session)
    setup_personas(session, case)
    hunter = FakeHunter(contacts=[hunter_contact("Zed", "Hidden", "CTO", "z@x.test")])
    service = make_service(session, case, hunter=hunter)

    summary = service.discover(case["relationship"].id, allow_provider_credits=False)

    assert hunter.calls == {"search": 0, "find": 0, "verify": 0}
    assert {c["full_name"] for c in summary["contacts"]} == {
        "Sara Ahmed",
        "Omar Khalid",
        "Lina Haddad",
    }
    assert "PROVIDER_CREDITS_NOT_ALLOWED" in summary["run"]["operations"]["notes"]
    assert not session.scalars(
        select(ProviderUsage).where(ProviderUsage.provider_key == "HUNTER")
    ).all()
    # Missing emails are reported, never invented.
    assert summary["outcome"] == "CONTACT_FOUND_EMAIL_MISSING"
    assert all(c["email"] is None for c in summary["contacts"])
    assert not session.scalars(select(ContactEmail)).all()
    sara = by_name(summary, "Sara Ahmed")
    assert sara["employment_status"] == "CURRENT_CONFIRMED"
    assert sara["linkedin_url"] == "https://www.linkedin.com/in/sara-ahmed-123"
    assert sara["contact_status"] == "CONTACT_FOUND_EMAIL_MISSING"


def test_credits_require_permission(session):
    case = seed_case(session, role=MembershipRole.READ_ONLY)
    setup_personas(session, case)
    service = make_service(session, case, hunter=FakeHunter())
    with pytest.raises(AuthorizationError):
        service.discover(case["relationship"].id, allow_provider_credits=True)


# 7 + 10. Dedupe and strong current employee -------------------------------------------------
def test_website_and_provider_records_merge_into_one_confirmed_ready_contact(session):
    case = seed_case(session)
    setup_personas(session, case)
    hunter = FakeHunter(
        contacts=[
            hunter_contact(
                "Sara",
                "Ahmed",
                "CIO",
                "Sara@Example.test",
                department="it",
                seniority="executive",
                sources=still_listed(),
                verification_status="valid",
                verification_date="2026-09-12",
            )
        ]
    )
    service = make_service(session, case, hunter=hunter)

    summary = service.discover(case["relationship"].id, allow_provider_credits=True)

    saras = [c for c in summary["contacts"] if c["full_name"] == "Sara Ahmed"]
    assert len(saras) == 1
    sara = saras[0]
    assert hunter.search_args["departments"] == ["executive", "hr", "it", "management"]
    assert hunter.search_args["seniorities"] == ["executive", "senior"]
    assert sara["job_title"] == "Chief Information Officer"  # company website wins
    assert sara["email"] == "sara@example.test"
    assert sara["email_state"] == "VERIFIED"
    assert sara["employment_status"] == "CURRENT_CONFIRMED"
    assert sara["suitability"] == "HIGH"
    assert sara["contact_status"] == "READY_FOR_DRAFT"
    assert sara["persona_rank"] == 1
    assert summary["outcome"] == "READY_FOR_DRAFT"
    # Provider-reported recent verification avoided a second paid check.
    assert hunter.calls["verify"] == 0
    detail = service.detail(case["relationship"].id, sara["id"])
    types = {e["source_type"] for e in detail["evidence"]}
    assert {"COMPANY_WEBSITE", "PROVIDER_RECORD", "PROVIDER_SOURCE_PAGE"} <= types
    assert all(e["retrieved_at"] for e in detail["evidence"])
    assert detail["emails"][0]["origin"] == "PROVIDER_FOUND"
    assert detail["emails"][0]["verification_source"] == "PROVIDER_REPORTED"
    contact = session.get(ContactCandidate, sara["id"])
    assert contact.buyer_role_match.value in {"EXACT", "STRONG"}


def test_rerun_uses_cache_and_does_not_duplicate(session):
    case = seed_case(session)
    setup_personas(session, case)
    hunter = FakeHunter(contacts=[hunter_contact("Omar", "Khalid", "CEO", sources=still_listed())])
    public = FakePublic()
    service = make_service(session, case, public=public, hunter=hunter)
    service.discover(case["relationship"].id, allow_provider_credits=True)
    fetched = len(public.fetched)
    again = service.discover(case["relationship"].id, allow_provider_credits=True)
    assert hunter.calls["search"] == 1
    assert len(public.fetched) == fetched
    assert again["run"]["operations"]["provider_credits"]["hunter_cached"] is True
    assert len([c for c in again["contacts"] if c["full_name"] == "Omar Khalid"]) == 1
    refreshed = service.discover(
        case["relationship"].id, allow_provider_credits=True, force_refresh=True
    )
    assert hunter.calls["search"] == 2
    assert len(refreshed["contacts"]) == 3


# 8. Former employees ---------------------------------------------------------------------------
def test_former_employee_is_rejected_and_blocked_from_campaigns(session):
    case = seed_case(session)
    setup_personas(session, case)
    hunter = FakeHunter(
        contacts=[
            hunter_contact(
                "Karim",
                "Nabil",
                "Former CTO",
                "karim@x.test",
                sources=(ProviderContactSource(uri="https://old.test/p", still_on_page=False),),
            ),
            hunter_contact(
                "Mona",
                "Saleh",
                "IT Director",
                "mona@x.test",
                sources=(ProviderContactSource(uri="https://old.test/q", still_on_page=False),),
            ),
        ],
        verification=lambda email: "invalid" if email.startswith("mona") else "valid",
    )
    service = make_service(session, case, public=FakePublic({}), hunter=hunter)

    summary = service.discover(case["relationship"].id, allow_provider_credits=True)

    karim = by_name(summary, "Karim Nabil")
    assert karim["role_match"] == "NONE"
    assert karim["employment_status"] == "FORMER"
    assert karim["persona_rank"] is None
    mona = by_name(summary, "Mona Saleh")
    assert mona["employment_status"] == "FORMER"
    assert mona["contact_status"] == "NOT_RELEVANT"
    assert mona["persona_rank"] is None
    contact = session.get(ContactCandidate, mona["id"])
    assert targeting_block_reason(contact) == "CONTACT_EMPLOYMENT_NOT_CURRENT"
    with pytest.raises(ConflictError):
        CampaignDraftService(session, case["tenant"]).generate(
            case["relationship"].id, case["qualification"].id, contact.id
        )


def test_person_removed_from_company_page_is_no_longer_confirmed(session):
    case = seed_case(session)
    setup_personas(session, case)
    public = FakePublic()
    service = make_service(session, case, public=public)
    service.discover(case["relationship"].id)
    public.pages["/about-us/leadership"] = LEADERSHIP.replace(
        '<div class="member"><h3>Lina Haddad</h3><p>HR Director</p></div>', ""
    )
    summary = service.discover(case["relationship"].id, force_refresh=True)
    lina = by_name(summary, "Lina Haddad")
    assert lina["employment_status"] == "CURRENT_UNCERTAIN"
    assert lina["contact_status"] == "INSUFFICIENT_EVIDENCE"


def test_conflicting_titles_are_never_auto_targeted():
    evidence = [
        EvidenceView(EvidenceSourceType.COMPANY_WEBSITE, "https://x/team", "Head of Sales", NOW),
        EvidenceView(EvidenceSourceType.PROVIDER_RECORD, None, "Chief Information Officer", NOW),
    ]
    status, _ = employment_status(evidence, [], NOW)
    assert status == EmploymentStatus.CONFLICTING
    grade, _ = suitability(RoleMatch.HIGH, status, evidence, None)
    assert grade == Suitability.INSUFFICIENT_EVIDENCE
    assert not titles_conflict("CIO", "Chief Information Officer")
    assert not titles_conflict("IT Director", "Group Director of IT")


# 9. Weak evidence ------------------------------------------------------------------------------
def test_single_uncorroborated_provider_record_is_insufficient_evidence(session):
    case = seed_case(session)
    setup_personas(session, case)
    hunter = FakeHunter(contacts=[hunter_contact("Hani", "Fawzi", "CTO", "hani@x.test")])
    service = make_service(session, case, public=FakePublic({}), hunter=hunter)
    summary = service.discover(case["relationship"].id, allow_provider_credits=True)
    hani = by_name(summary, "Hani Fawzi")
    assert hani["employment_status"] == "CURRENT_UNCERTAIN"
    assert hani["suitability"] == "INSUFFICIENT_EVIDENCE"
    assert hani["contact_status"] == "INSUFFICIENT_EVIDENCE"
    assert summary["outcome"] == "INSUFFICIENT_EVIDENCE"
    # No paid email work is spent on people who were not selected.
    assert hunter.calls["verify"] == 0 and hunter.calls["find"] == 0


def test_no_people_and_no_relevant_people_outcomes(session):
    case = seed_case(session)
    setup_personas(session, case)
    empty = make_service(session, case, public=FakePublic({}), hunter=FakeHunter())
    assert empty.discover(case["relationship"].id, allow_provider_credits=True)["outcome"] is None
    assert empty.summary(case["relationship"].id)["last_run"]["outcome"] == "NO_CONTACTS_FOUND"

    other = seed_case(session)
    setup_personas(session, other)
    hunter = FakeHunter(
        contacts=[hunter_contact("Ali", "Samir", "Sales Executive", sources=still_listed())]
    )
    service = make_service(session, other, public=FakePublic({}), hunter=hunter)
    summary = service.discover(other["relationship"].id, allow_provider_credits=True)
    assert summary["outcome"] == "NO_RELEVANT_DECISION_MAKER"


# 11-12. Email discovery and verification states ----------------------------------------------
def test_email_find_and_verification_only_for_ranked_people(session):
    case = seed_case(session)
    setup_personas(session, case)

    def found(person):
        if person["first_name"] == "Omar":
            return ProviderContact(
                first_name="Omar",
                last_name="Khalid",
                email="omar@example.test",
                job_title="CEO",
                confidence=88,
            )
        return None

    hunter = FakeHunter(found=found, verification="accept_all")
    service = make_service(session, case, hunter=hunter)
    summary = service.discover(case["relationship"].id, allow_provider_credits=True)

    omar = by_name(summary, "Omar Khalid")
    assert omar["email"] == "omar@example.test"
    assert omar["email_state"] == "UNVERIFIED"
    assert omar["contact_status"] == "EMAIL_UNVERIFIED"
    lina = by_name(summary, "Lina Haddad")
    assert lina["email"] is None and lina["email_state"] == "UNKNOWN"
    assert lina["contact_status"] == "CONTACT_FOUND_EMAIL_MISSING"
    assert hunter.calls["find"] == 3  # one per ranked person, no guessing for the misses
    assert hunter.calls["verify"] == 1
    assert summary["outcome"] == "EMAIL_UNVERIFIED"
    assert (
        session.scalar(
            select(ProviderUsage).where(ProviderUsage.operation == "EMAIL_VERIFY")
        ).credits_used
        == 1
    )

    # A second run does not re-buy finder lookups that already came back empty.
    service.discover(case["relationship"].id, allow_provider_credits=True, force_refresh=False)
    assert hunter.calls["find"] == 3


def test_email_display_states():
    assert email_state(None) == "UNKNOWN"
    assert email_state(EmailView("a@x", EmailVerificationStatus.VALID, NOW)) == "VERIFIED"
    assert email_state(EmailView("a@x", EmailVerificationStatus.INVALID, NOW)) == "INVALID"
    assert email_state(EmailView("a@x", EmailVerificationStatus.RISKY, NOW)) == "UNVERIFIED"
    assert (
        email_state(EmailView("a@x", EmailVerificationStatus.UNKNOWN, None, "PROVIDER_FOUND"))
        == "PROVIDER_FOUND"
    )
    assert (
        email_state(EmailView("a@x", EmailVerificationStatus.UNKNOWN, None, "PATTERN_PREDICTED"))
        == "PATTERN_PREDICTED"
    )


def test_stale_provider_verification_is_not_trusted(session):
    case = seed_case(session)
    setup_personas(session, case)
    hunter = FakeHunter(
        contacts=[
            hunter_contact(
                "Sara",
                "Ahmed",
                "CIO",
                "sara@example.test",
                sources=still_listed(),
                verification_status="valid",
                verification_date="2024-01-01",
            )
        ],
        verification="invalid",
    )
    service = make_service(session, case, hunter=hunter)
    summary = service.discover(case["relationship"].id, allow_provider_credits=True)
    sara = by_name(summary, "Sara Ahmed")
    assert hunter.calls["verify"] == 1
    assert sara["email_state"] == "INVALID"
    assert sara["contact_status"] == "CONTACT_FOUND_EMAIL_MISSING"


# 13-14. Workflow gating --------------------------------------------------------------------------
def test_workflow_stays_blocked_until_qualified(session):
    case = seed_case(session, qualification_status=QualificationStatus.NEEDS_MORE_RESEARCH)
    setup_personas(session, case)
    service = make_service(session, case, hunter=FakeHunter())
    with pytest.raises(ConflictError, match="DECISION_MAKERS_NOT_QUALIFIED"):
        service.discover(case["relationship"].id)
    orchestration = AutomationOrchestrationService(
        session,
        case["tenant"],
        ProviderRegistry([FakePublic()]),
        decision_maker_factory=lambda db, tenant: make_service(db, case),
    )
    plan = orchestration.plan(case["relationship"].id, case["qualification"].product_id)
    step = next(s for s in plan["steps"] if s["step"] == "DISCOVER_CONTACTS")
    assert plan["contact_mode"] == "DECISION_MAKERS"
    assert step["status"] == "BLOCKED"


def test_workflow_continues_after_qualification_and_gates_on_verified_person(session):
    case = seed_case(session)
    setup_personas(session, case)
    hunter = FakeHunter(
        contacts=[
            hunter_contact(
                "Omar",
                "Khalid",
                "CEO",
                "omar@example.test",
                sources=still_listed(),
            )
        ],
        verification="valid",
    )
    public = FakePublic()
    registry = ProviderRegistry([public, hunter])
    orchestration = AutomationOrchestrationService(
        session,
        case["tenant"],
        registry,
        decision_maker_factory=lambda db, tenant: make_service(
            db, case, public=public, hunter=hunter
        ),
    )
    product_id = case["qualification"].product_id
    before = orchestration.plan(case["relationship"].id, product_id)
    step = next(s for s in before["steps"] if s["step"] == "DISCOVER_CONTACTS")
    assert step["status"] == "READY" and step["requires_provider_credits"] is False

    public_only = orchestration.run_until_gate(case["relationship"].id, product_id)
    assert public_only["executed_steps"][-1]["step"] == "DISCOVER_CONTACTS"
    assert public_only["stop_reason"] == "DECISION_MAKERS_CONTACT_FOUND_EMAIL_MISSING"
    assert public_only["plan"]["state"] == "IN_PROGRESS"
    assert hunter.calls["search"] == 0

    # Without credits a repeat run does not search again.
    repeat = orchestration.run_until_gate(case["relationship"].id, product_id)
    assert repeat["executed_steps"] == []

    with_credits = orchestration.run_until_gate(
        case["relationship"].id, product_id, allow_provider_credits=True
    )
    assert hunter.calls["search"] == 1
    assert with_credits["stop_reason"] == "HUMAN_CAMPAIGN_GATE"
    assert with_credits["plan"]["state"] == "READY_FOR_HUMAN_CAMPAIGN_REVIEW"
    assert with_credits["plan"]["contact_count"] == 1


def test_products_without_personas_keep_legacy_contact_step(session):
    case = seed_case(session)
    orchestration = AutomationOrchestrationService(
        session, case["tenant"], ProviderRegistry([FakePublic()])
    )
    plan = orchestration.plan(case["relationship"].id, case["qualification"].product_id)
    step = next(s for s in plan["steps"] if s["step"] == "DISCOVER_CONTACTS")
    assert plan["contact_mode"] == "LEGACY"
    assert step["requires_provider_credits"] is True


# 15. Ranking -----------------------------------------------------------------------------------
def test_best_people_per_persona_are_ranked_and_capped(session):
    case = seed_case(session)
    setup_personas(session, case)
    hunter = FakeHunter(
        contacts=[
            hunter_contact("Tarek", "Aziz", "CTO", "tarek@x.test", sources=still_listed()),
            hunter_contact("Nour", "Adel", "IT Director", "nour@x.test", sources=still_listed()),
            hunter_contact(
                "Rami", "Fouad", "Head of IT", "rami@x.test", sources=still_listed("https://r/1")
            ),
        ],
        verification="valid",
    )
    service = make_service(session, case, hunter=hunter)
    summary = service.discover(case["relationship"].id, allow_provider_credits=True)
    it_people = [c for c in summary["contacts"] if c["persona_name"] == "IT leadership"]
    ranked = sorted([c for c in it_people if c["persona_rank"]], key=lambda c: c["persona_rank"])
    assert len(ranked) == 2  # max_contacts for IT leadership
    # Evidence quality ranks first: the CIO confirmed on the company site leads even though
    # no email was found for her; the runner-up is a verified provider contact.
    assert ranked[0]["full_name"] == "Sara Ahmed"
    assert ranked[0]["contact_status"] == "CONTACT_FOUND_EMAIL_MISSING"
    assert ranked[1]["contact_status"] == "READY_FOR_DRAFT"
    assert hunter.calls["find"] >= 1  # the top-ranked person was looked up, not guessed
    persona = next(p for p in summary["personas"] if p["name"] == "IT leadership")
    assert persona["selected"] == [c["id"] for c in ranked] or set(persona["selected"]) == {
        c["id"] for c in ranked
    }
    execs = [c for c in summary["contacts"] if c["persona_name"] == "Executive leadership"]
    assert len([c for c in execs if c["persona_rank"]]) <= 1

    unranked = next(c for c in it_people if not c["persona_rank"])
    approved = service.review(case["relationship"].id, unranked["id"], "APPROVE", "Known buyer")
    assert approved["persona_rank"] == 1


# 16. Cross-tenant isolation --------------------------------------------------------------------
def test_other_tenants_cannot_read_or_change_people(session):
    case = seed_case(session)
    setup_personas(session, case)
    summary = make_service(session, case).discover(case["relationship"].id)
    contact_id = summary["contacts"][0]["id"]

    other = seed_case(session)
    intruder = make_service(session, other, tenant=admin(other))
    with pytest.raises(NotFoundError):
        intruder.summary(case["relationship"].id)
    with pytest.raises(NotFoundError):
        intruder.detail(case["relationship"].id, contact_id)
    with pytest.raises(NotFoundError):
        intruder.detail(other["relationship"].id, contact_id)
    with pytest.raises(NotFoundError):
        intruder.review(other["relationship"].id, contact_id, "APPROVE")
    with pytest.raises(NotFoundError):
        intruder.replace_personas(case["qualification"].product_id, [{"name": "X"}])
    assert all(
        e.organization_id == case["org"].id for e in session.scalars(select(ContactEvidence))
    )


# 17. Override audit ----------------------------------------------------------------------------
def test_manual_overrides_are_audited_and_enforced(session):
    case = seed_case(session)
    setup_personas(session, case)
    service = make_service(session, case)
    summary = service.discover(case["relationship"].id)
    omar = by_name(summary, "Omar Khalid")

    excluded = service.review(case["relationship"].id, omar["id"], "EXCLUDE", "Not a buyer")
    assert excluded["contact_status"] == ContactStatus.EXCLUDED
    assert excluded["persona_rank"] is None
    contact = session.get(ContactCandidate, omar["id"])
    assert targeting_block_reason(contact) == "CONTACT_EXCLUDED"
    entry = session.scalar(
        select(AuditLog).where(
            AuditLog.action == "decision_makers.review", AuditLog.entity_id == omar["id"]
        )
    )
    assert entry.actor_user_id == case["user"].id
    assert entry.metadata_json["before"]["contact_status"] == "CONTACT_FOUND_EMAIL_MISSING"
    assert entry.metadata_json["after"]["review_status"] == "EXCLUDED"
    assert entry.metadata_json["note"] == "Not a buyer"
    assert excluded["history"][0]["action"] == "decision_makers.review"

    reset = service.review(case["relationship"].id, omar["id"], "RESET")
    assert reset["review_status"] is None
    assert reset["contact_status"] == "CONTACT_FOUND_EMAIL_MISSING"

    reader = make_service(
        session,
        case,
        tenant=TenantContext(case["org"].id, case["user"].id, MembershipRole.READ_ONLY),
    )
    with pytest.raises(AuthorizationError):
        reader.review(case["relationship"].id, omar["id"], "APPROVE")
    with pytest.raises(ConflictError):
        service.review(case["relationship"].id, omar["id"], "DELETE")


# 18. AI parsing and validation -----------------------------------------------------------------
def test_ai_results_are_validated_before_use():
    candidates = [
        PersonCandidate("a", "CPO", None, None),
        PersonCandidate("b", "Digital Transformation Lead", "it", None),
        PersonCandidate("c", "Chief Executive Officer", None, None),
    ]
    ai = FakeAI(
        roles=[
            {
                "candidate_id": "a",
                "persona": "HR leadership",
                "normalized_role": "Chief People Officer",
                "role_match": "HIGH",
                "confidence": 150,
                "reason": "CPO here means Chief People Officer   given HR context",
            },
            {"candidate_id": "b", "persona": "Made-up persona", "role_match": "HIGH"},
            {"candidate_id": "zz", "persona": "IT leadership", "role_match": "HIGH"},
            {"candidate_id": "c", "persona": "IT leadership", "role_match": "HIGH"},
            {"candidate_id": "b", "persona": None, "role_match": "MAYBE"},
        ]
    )
    resolver = RoleResolver(ai)
    results = resolver.resolve_many(candidates, SPECS)
    # Only ambiguous titles are sent; exact rule matches never reach the model.
    assert [c["candidate_id"] for c in ai.last_candidates] == ["a", "b"]
    assert results["a"].persona.name == "HR leadership"
    assert results["a"].resolver == "AI" and results["a"].confidence == 90
    assert results["a"].reason == "CPO here means Chief People Officer given HR context"
    assert results["b"].resolver == "RULES"  # invented persona / invalid enum ignored
    assert results["c"].persona.name == "Executive leadership" and results["c"].resolver == "RULES"


def test_openai_client_uses_structured_output_and_parses_response(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("ROLE_RESOLVER_MODEL", "cheap-model")
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        captured["auth"] = request.headers["authorization"]
        payload = {
            "results": [
                {
                    "candidate_id": "a",
                    "persona": "HR leadership",
                    "normalized_role": "chief people officer",
                    "role_match": "HIGH",
                    "confidence": 80,
                    "reason": "Title names the people function",
                }
            ]
        }
        return httpx.Response(
            200,
            json={
                "output": [{"content": [{"type": "output_text", "text": json.dumps(payload)}]}],
                "usage": {"input_tokens": 120, "output_tokens": 30},
            },
        )

    client = OpenAIDecisionMakerClient(httpx.Client(transport=httpx.MockTransport(handler)))
    results = client.resolve_roles([{"candidate_id": "a", "title": "CPO"}], [{"name": "HR"}])
    assert results[0]["persona"] == "HR leadership"
    assert captured["body"]["model"] == "cheap-model"
    assert captured["body"]["text"]["format"]["type"] == "json_schema"
    assert captured["body"]["text"]["format"]["strict"] is True
    assert captured["auth"] == "Bearer test-key"
    assert client.last_usage == {"input_tokens": 120, "output_tokens": 30}

    def bad(request):
        return httpx.Response(200, json={"output": [{"content": [{"text": "not json"}]}]})

    broken = OpenAIDecisionMakerClient(httpx.Client(transport=httpx.MockTransport(bad)))
    with pytest.raises(DecisionMakerAIError, match="INVALID_PROVIDER_RESPONSE"):
        broken.resolve_roles([], [])
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(DecisionMakerAIError, match="NOT_CONFIGURED"):
        OpenAIDecisionMakerClient().resolve_roles([], [])


def test_ai_page_extraction_rejects_people_not_on_the_page(session):
    case = seed_case(session)
    setup_personas(session, case)
    page = (
        "<html><body><div><p>Leadership. Our company is led by Yasmin Farouk, who serves as "
        "Chief Technology Officer, together with a great team of professionals building the "
        "future of our region and customers across many industries and sectors today."
        "</p></div></body></html>"
    )
    public = FakePublic({"/": HOME, "/about-us/leadership": page})
    ai = FakeAI(
        people=[
            {"name": "Yasmin Farouk", "title": "Chief Technology Officer"},
            {"name": "Invented Person", "title": "Chief Executive Officer"},
            {"name": "Yasmin Farouk", "title": "Chief Wizard"},
        ]
    )
    service = make_service(session, case, public=public, ai=ai)
    summary = service.discover(case["relationship"].id)
    assert [c["full_name"] for c in summary["contacts"]] == ["Yasmin Farouk"]
    assert summary["contacts"][0]["persona_name"] == "IT leadership"
    assert ai.calls["people"] == 1


# 19. AI failure fallback -----------------------------------------------------------------------
def test_ai_failure_falls_back_to_rules_and_is_recorded(session):
    case = seed_case(session)
    setup_personas(session, case)
    page = LEADERSHIP.replace("HR Director", "People &amp; Culture Lead")
    ai = FakeAI(error=DecisionMakerAIError("RATE_LIMITED"))
    service = make_service(
        session, case, public=FakePublic({"/": HOME, "/about-us/leadership": page}), ai=ai
    )
    summary = service.discover(case["relationship"].id)
    lina = by_name(summary, "Lina Haddad")
    assert lina["role_resolver"] == "RULES"
    assert lina["persona_name"] == "HR leadership"
    assert summary["run"]["operations"]["openai"]["errors"] == ["RATE_LIMITED"]
    usage = session.scalar(select(ProviderUsage).where(ProviderUsage.provider_key == "OPENAI"))
    assert usage.operation == "ROLE_RESOLUTION" and usage.response_status == "FAILURE"


# Extra safety checks ---------------------------------------------------------------------------
def test_leadership_extraction_and_linkedin_links_are_never_fetched():
    public = FakePublic()
    scan = LeadershipPageScanner(public, max_pages=3).scan("acme.example")
    assert all("linkedin.com" not in url for url in public.fetched)
    assert all(url.startswith("https://acme.example") for url in public.fetched)
    names = [p.name for page in scan.pages for p in page.people]
    assert names == ["Sara Ahmed", "Omar Khalid", "Lina Haddad"]
    assert extract_people(["Our Clients", "Trusted by leaders"], []) == []
    assert normalize_linkedin("linkedin.com/in/Some-One/?trk=x") == (
        "https://www.linkedin.com/in/some-one"
    )
    assert normalize_linkedin("https://www.linkedin.com/company/acme") is None
    assert normalize_linkedin("https://evil.test/in/abc") is None


def test_employment_rules_use_recency():
    fresh = [
        EvidenceView(
            EvidenceSourceType.PROVIDER_SOURCE_PAGE,
            "https://a",
            None,
            NOW,
            NOW - timedelta(days=30),
            True,
        )
    ]
    assert employment_status(fresh, [], NOW)[0] == EmploymentStatus.CURRENT_LIKELY
    old_site = [
        EvidenceView(
            EvidenceSourceType.COMPANY_WEBSITE, "https://t", "CIO", NOW - timedelta(days=400)
        )
    ]
    assert employment_status(old_site, [], NOW)[0] == EmploymentStatus.CURRENT_UNCERTAIN
    assert employment_status([], [], datetime(2026, 1, 1, tzinfo=UTC))[0] == (
        EmploymentStatus.CURRENT_UNCERTAIN
    )


# API ---------------------------------------------------------------------------------------------
def test_api_routes_enforce_rbac_and_run_the_flow(tmp_path):
    from fastapi.testclient import TestClient
    from test_contacts_api import SECRET, login, seed_api_database

    from marketingiq.api.app import create_app

    url = f"sqlite+pysqlite:///{tmp_path / 'dm-api.db'}"
    seeded = seed_api_database(url)
    app = create_app(url, SECRET)
    public = FakePublic()
    hunter = FakeHunter(configured=False)
    app.state.decision_maker_factory = lambda db, tenant: DecisionMakerService(
        db,
        tenant,
        ProviderRegistry([public, hunter]),
        ai_client=None,
        page_scanner=LeadershipPageScanner(public),
    )
    client = TestClient(app)
    marketing = login(client, seeded["marketing_email"])
    reader = login(client, seeded["reader_email"])
    org = f"/api/v1/organizations/{seeded['org_id']}"
    product_id = client.get(f"{org}/products", headers=reader).json()[0]["id"]

    assert client.get(f"{org}/products/{product_id}/personas", headers=reader).status_code == 200
    body = {"personas": PERSONAS}
    assert (
        client.put(f"{org}/products/{product_id}/personas", headers=reader, json=body).status_code
        == 403
    )
    too_many = {"personas": [{"name": "x", "max_contacts": 4}]}
    assert (
        client.put(
            f"{org}/products/{product_id}/personas", headers=marketing, json=too_many
        ).status_code
        == 422
    )
    saved = client.put(f"{org}/products/{product_id}/personas", headers=marketing, json=body)
    assert saved.status_code == 200 and len(saved.json()["personas"]) == 3

    base = f"{org}/companies/{seeded['relationship_id']}/decision-makers"
    assert client.post(f"{base}/discover", headers=reader, json={}).status_code == 403
    run = client.post(f"{base}/discover", headers=marketing, json={})
    assert run.status_code == 200, run.text
    assert run.json()["run"]["outcome"] == "CONTACT_FOUND_EMAIL_MISSING"
    listing = client.get(base, headers=reader).json()
    assert {c["full_name"] for c in listing["contacts"]} == {
        "Sara Ahmed",
        "Omar Khalid",
        "Lina Haddad",
    }
    contact_id = listing["contacts"][0]["id"]
    detail = client.get(f"{base}/{contact_id}", headers=reader)
    assert detail.status_code == 200 and detail.json()["evidence"]
    assert (
        client.post(
            f"{base}/{contact_id}/review", headers=marketing, json={"action": "DELETE"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"{base}/{contact_id}/review", headers=reader, json={"action": "EXCLUDE"}
        ).status_code
        == 403
    )
    excluded = client.post(
        f"{base}/{contact_id}/review", headers=marketing, json={"action": "EXCLUDE"}
    )
    assert excluded.status_code == 200 and excluded.json()["contact_status"] == "EXCLUDED"
    no_hunter = client.post(f"{base}/{contact_id}/find-email", headers=marketing)
    assert no_hunter.status_code == 409
    assert hunter.calls == {"search": 0, "find": 0, "verify": 0}


# Migration ---------------------------------------------------------------------------------------
def test_migration_creates_tables_and_is_idempotent():
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect

    from marketingiq.domain.models import Base

    path = Path(__file__).parents[1] / "migrations/versions/20260923_21_decision_makers.py"
    spec = importlib.util.spec_from_file_location("migration_21", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.down_revision == "20260923_20"

    engine = create_engine("sqlite+pysqlite:///:memory:")
    legacy = [
        t
        for t in Base.metadata.sorted_tables
        if t.name not in {"product_personas", "contact_evidence"}
    ]
    Base.metadata.create_all(engine, tables=legacy)
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
            module.upgrade()  # re-running must not fail
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        assert {"product_personas", "contact_evidence"} <= tables
        columns = {c["name"] for c in inspector.get_columns("contact_candidates")}
        assert {"persona_name", "employment_status", "suitability", "review_status"} <= columns
        assert {"origin", "verification_source"} <= {
            c["name"] for c in inspector.get_columns("contact_emails")
        }


# Regression tests from review ------------------------------------------------------------------
def _requalify(session, case):
    from marketingiq.application.qualification import LeadQualificationService

    qualification = LeadQualificationService(
        session, admin(case), NOW + timedelta(minutes=5)
    ).execute(case["relationship"].id, case["assessment"].id)
    qualification.status = QualificationStatus.HIGH_PRIORITY
    session.flush()
    return qualification


def _second_product(session, case):
    from marketingiq.application.qualification import LeadQualificationService
    from marketingiq.domain.models import ICP, Product, ProductFitAssessment

    first = case["assessment"]
    product = Product(
        organization_id=case["org"].id,
        name="Second",
        slug="second-" + case["org"].slug,
        primary_buyer_roles=["CIO"],
        secondary_buyer_roles=[],
    )
    session.add(product)
    session.flush()
    icp = ICP(organization_id=case["org"].id, product_id=product.id, name="ICP2", version=1)
    session.add(icp)
    session.flush()
    icp.logical_id = icp.id
    assessment = ProductFitAssessment(
        **{
            k: getattr(first, k)
            for k in (
                "organization_id",
                "company_id",
                "organization_company_id",
                "icp_version",
                "score",
                "evidence_coverage",
                "confidence",
                "grade",
                "status",
                "evaluated_at",
                "workflow_version",
                "evidence_snapshot",
                "explanation",
                "classification",
                "created_by_user_id",
            )
        },
        product_id=product.id,
        icp_id=icp.id,
    )
    session.add(assessment)
    session.flush()
    qualification = LeadQualificationService(session, admin(case), NOW).execute(
        case["relationship"].id, assessment.id
    )
    qualification.status = QualificationStatus.QUALIFIED
    session.flush()
    return product, qualification


def test_requalification_carries_people_forward_without_new_spend(session):
    case = seed_case(session)
    setup_personas(session, case)
    hunter = FakeHunter(
        contacts=[hunter_contact("Omar", "Khalid", "CEO", "omar@x.test", sources=still_listed())]
    )
    service = make_service(session, case, hunter=hunter)
    service.discover(case["relationship"].id, allow_provider_credits=True)
    product_id = case["qualification"].product_id
    new_q = _requalify(session, case)
    again = service.discover(case["relationship"].id, allow_provider_credits=True)
    assert hunter.calls["search"] == 1  # cached, no second purchase
    assert again["run"]["outcome"] == "READY_FOR_DRAFT"
    assert service.ready_count(case["relationship"].id, product_id, new_q.id) == 1
    orchestration = AutomationOrchestrationService(
        session,
        case["tenant"],
        ProviderRegistry([FakePublic(), hunter]),
        decision_maker_factory=lambda db, tenant: service,
    )
    plan = orchestration.plan(case["relationship"].id, product_id)
    assert plan["state"] == "READY_FOR_HUMAN_CAMPAIGN_REVIEW"


def test_second_product_gets_its_own_people(session):
    case = seed_case(session)
    setup_personas(session, case)
    service = make_service(session, case)
    service.discover(case["relationship"].id)
    product, _ = _second_product(session, case)
    second = service.discover(case["relationship"].id, product.id)
    assert {c["full_name"] for c in second["contacts"]} == {
        "Sara Ahmed",
        "Omar Khalid",
        "Lina Haddad",
    }
    sara = by_name(second, "Sara Ahmed")
    assert sara["persona_name"] == "CIO"


def test_credit_run_is_final_for_automation_until_requalified(session):
    case = seed_case(session)
    setup_personas(session, case)
    hunter = FakeHunter(verification="accept_all")
    service = make_service(session, case, hunter=hunter)
    service.discover(case["relationship"].id, allow_provider_credits=True)
    later = make_service(session, case, hunter=hunter)
    later.now = NOW + timedelta(days=30)
    orchestration = AutomationOrchestrationService(
        session,
        case["tenant"],
        ProviderRegistry([FakePublic(), hunter]),
        decision_maker_factory=lambda db, tenant: later,
    )
    product_id = case["qualification"].product_id
    plan = orchestration.plan(case["relationship"].id, product_id)
    step = next(s for s in plan["steps"] if s["step"] == "DISCOVER_CONTACTS")
    assert step["status"] == "BLOCKED" and step["credit_retry_available"] is False
    result = orchestration.run_until_gate(
        case["relationship"].id, product_id, allow_provider_credits=True
    )
    assert result["executed_steps"] == []


def test_offsite_redirects_are_never_followed_or_attributed():
    public = FakePublic({"/": HOME, "/about-us/leadership": LEADERSHIP})
    public.redirects["https://acme.example/"] = "https://other-company.test/"
    scan = LeadershipPageScanner(public).scan("acme.example")
    assert all("other-company" not in p.url for p in scan.pages)

    public = FakePublic({"/": HOME})
    public.redirects["https://acme.example/about-us/leadership"] = (
        "https://www.linkedin.com/company/acme"
    )
    scan = LeadershipPageScanner(public).scan("acme.example")
    assert scan.pages == [] or all("linkedin" not in p.url for p in scan.pages)
    assert "PROVIDER_ERROR" in scan.errors


def test_linkedin_link_is_attributed_only_to_the_adjacent_person():
    nodes = ["Sara Ahmed", "Chief Information Officer", "Omar Khalid", "Chief Executive Officer"]
    links = [("https://www.linkedin.com/in/omar-k", "", 2)]
    people = {p.name: p.linkedin_url for p in extract_people(nodes, links)}
    assert people["Sara Ahmed"] is None
    assert people["Omar Khalid"] == "https://www.linkedin.com/in/omar-k"
    ambiguous = [
        ("https://www.linkedin.com/in/a-one", "", 0),
        ("https://www.linkedin.com/in/b-two", "", 1),
    ]
    assert extract_people(nodes[:2], ambiguous)[0].linkedin_url is None


def test_persona_names_can_be_swapped_and_renamed(session):
    case = seed_case(session)
    service = setup_personas(session, case)
    product_id = case["qualification"].product_id
    current = service.list_personas(product_id)["personas"]
    swapped = [
        {**current[0], "name": current[1]["name"]},
        {**current[1], "name": current[0]["name"]},
    ]
    result = service.replace_personas(product_id, swapped)
    assert [p["name"] for p in result["personas"]] == [current[1]["name"], current[0]["name"]]
    renamed = service.replace_personas(product_id, [{**current[2], "name": current[0]["name"]}])
    assert [p["name"] for p in renamed["personas"]] == [current[0]["name"]]


def test_former_markers_do_not_misfire():
    assert "former" not in resolve("Head of CX & EX").reason.lower()
    status, _ = employment_status(
        [EvidenceView(EvidenceSourceType.COMPANY_WEBSITE, "https://t", "Head of CX & EX", NOW)],
        [],
        NOW,
    )
    assert status == EmploymentStatus.CURRENT_CONFIRMED
    assert resolve("ex-CTO").role_match == RoleMatch.NONE


def test_ai_extraction_requires_whole_word_matches(session):
    case = seed_case(session)
    setup_personas(session, case)
    page = (
        "<html><body><div><p>Leadership: Wali Hassanein is our Chief Technology Officers lead "
        "and a long paragraph of company history that continues for a while to make the page "
        "long enough for extraction by the model in this test scenario today.</p></div>"
        "</body></html>"
    )
    ai = FakeAI(people=[{"name": "Ali Hassan", "title": "Chief Technology Officer"}])
    service = make_service(
        session, case, public=FakePublic({"/": HOME, "/about-us/leadership": page}), ai=ai
    )
    assert service.discover(case["relationship"].id)["contacts"] == []


def test_non_http_source_links_are_dropped(session):
    case = seed_case(session)
    setup_personas(session, case)
    hunter = FakeHunter(
        contacts=[
            hunter_contact(
                "Omar",
                "Khalid",
                "CEO",
                sources=(ProviderContactSource(uri="javascript:alert(1)", still_on_page=True),),
            )
        ]
    )
    service = make_service(session, case, public=FakePublic({}), hunter=hunter)
    summary = service.discover(case["relationship"].id, allow_provider_credits=True)
    detail = service.detail(case["relationship"].id, by_name(summary, "Omar Khalid")["id"])
    assert all(not (e["source_url"] or "").startswith("javascript") for e in detail["evidence"])
