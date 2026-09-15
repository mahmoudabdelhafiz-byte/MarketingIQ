from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from marketingiq.application.contacts import ContactDiscoveryService, match_buyer_role
from marketingiq.application.errors import AuthorizationError, ConflictError, NotFoundError
from marketingiq.application.qualification import LeadQualificationService
from marketingiq.application.research import ProviderRegistry
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.models import (
    ICP,
    AuditLog,
    BuyerRoleMatch,
    Company,
    CompanyFact,
    CompanyIdentifier,
    ContactCandidate,
    ContactEmail,
    DataClassification,
    EmailVerificationStatus,
    FitGrade,
    FitStatus,
    MembershipRole,
    Organization,
    OrganizationCompany,
    Product,
    ProductFitAssessment,
    ProviderUsage,
    QualificationStatus,
    RedistributionStatus,
    User,
)
from marketingiq.domain.providers import (
    ContactProviderResult,
    ProviderAuthenticationError,
    ProviderCapability,
    ProviderContact,
    ProviderError,
    ProviderMalformedResponse,
    ProviderNotConfigured,
    ProviderRateLimited,
)
from marketingiq.infrastructure.providers import HunterProvider

NOW = datetime(2026, 9, 15, 21, 0, tzinfo=UTC)


class FakeContactProvider:
    key = "HUNTER"
    capabilities = frozenset(
        {
            ProviderCapability.SEARCH_CONTACTS,
            ProviderCapability.FIND_EMAIL,
            ProviderCapability.VERIFY_EMAIL,
        }
    )
    costs_credits = True

    def __init__(
        self,
        *,
        contacts=(),
        found_email="found@example.test",
        verification="valid",
        error=None,
        credits_used=2,
        credits_remaining=8,
    ):
        self.contacts = tuple(contacts)
        self.found_email = found_email
        self.verification = verification
        self.error = error
        self.credits_used = credits_used
        self.credits_remaining = credits_remaining
        self.calls = {"search": 0, "find": 0, "verify": 0}

    @property
    def configured(self):
        return not isinstance(self.error, ProviderNotConfigured)

    def _maybe_raise(self):
        if self.error:
            raise self.error

    def search_contacts(self, company_identifier, max_results):
        self.calls["search"] += 1
        self._maybe_raise()
        return ContactProviderResult(
            self.key,
            self.contacts[:max_results],
            "request-search",
            self.credits_used,
            self.credits_remaining,
        )

    def find_email(self, person):
        self.calls["find"] += 1
        self._maybe_raise()
        contacts = (
            ProviderContact(
                reference="finder-ref",
                first_name=person.get("first_name"),
                last_name=person.get("last_name"),
                email=self.found_email,
                confidence=91,
            ),
        ) if self.found_email else ()
        return ContactProviderResult(
            self.key,
            contacts,
            "request-find",
            self.credits_used,
            self.credits_remaining,
        )

    def verify_email(self, email):
        self.calls["verify"] += 1
        self._maybe_raise()
        return ContactProviderResult(
            self.key,
            (ProviderContact(reference=self.verification, email=email, confidence=87),),
            "request-verify",
            self.credits_used,
            self.credits_remaining,
        )


def seed_case(
    session,
    *,
    role=MembershipRole.MARKETING_USER,
    qualification_status=QualificationStatus.HIGH_PRIORITY,
    buyer_role="VP Marketing",
):
    suffix = uuid4().hex[:10]
    org = Organization(name="Tenant", slug="contacts-" + suffix)
    user = User(email="contacts-" + suffix + "@test", password_hash="x")
    company = Company(canonical_name="Target")
    product = Product(
        organization=org,
        name="Product",
        slug="product-" + suffix,
        primary_buyer_roles=[buyer_role],
        secondary_buyer_roles=["Procurement"],
    )
    icp = ICP(organization_id=org.id, product=product, name="ICP", version=1, is_active=True)
    relationship = OrganizationCompany(organization=org, company=company)
    session.add_all([user, product, relationship])
    session.flush()
    icp.logical_id = icp.id
    session.add(
        CompanyIdentifier(
            company_id=company.id,
            kind="DOMAIN",
            value=f"{suffix}.example.test",
            normalized_value=f"{suffix}.example.test",
            is_primary=True,
        )
    )
    fact = CompanyFact(
        company_id=company.id,
        fact_key="industry",
        value="Software",
        classification=DataClassification.PUBLIC_EVIDENCE,
        redistribution_status=RedistributionStatus.ALLOWED,
        confidence=90,
        observed_at=NOW,
    )
    session.add(fact)
    session.flush()
    criteria = [
        {
            "criterion_id": "industry",
            "type": "industry",
            "expected_value": "Software",
            "result": "MATCH",
            "required": True,
            "fact_key": "industry",
            "actual_value": "Software",
            "selected_fact_id": fact.id,
            "confidence": 90,
            "quality": "CURRENT",
        }
    ]
    assessment = ProductFitAssessment(
        organization_id=org.id,
        company_id=company.id,
        organization_company_id=relationship.id,
        product_id=product.id,
        icp_id=icp.id,
        icp_version=1,
        score=90,
        evidence_coverage=90,
        confidence="HIGH",
        grade=FitGrade.A,
        status=FitStatus.COMPLETE,
        evaluated_at=NOW,
        workflow_version="test",
        evidence_snapshot=criteria,
        explanation={"criteria": criteria},
        classification=DataClassification.MARKETINGIQ_DERIVED,
        created_by_user_id=user.id,
    )
    session.add(assessment)
    session.flush()
    qualification = LeadQualificationService(
        session,
        TenantContext(org.id, user.id, MembershipRole.ORGANIZATION_ADMIN),
        NOW,
    ).execute(relationship.id, assessment.id)
    qualification.status = qualification_status
    session.flush()
    return {
        "org": org,
        "user": user,
        "company": company,
        "relationship": relationship,
        "assessment": assessment,
        "qualification": qualification,
        "tenant": TenantContext(org.id, user.id, role),
    }


def service_for(session, case, provider):
    return ContactDiscoveryService(
        session,
        case["tenant"],
        ProviderRegistry([provider]),
        NOW,
    )


def test_buyer_role_matching_covers_all_strengths_and_is_deterministic():
    assert match_buyer_role("VP Marketing", "VP Marketing", None, None)[0] == BuyerRoleMatch.EXACT
    assert (
        match_buyer_role("Chief Marketing Officer", "Chief Marketing", None, None)[0]
        == BuyerRoleMatch.STRONG
    )
    assert (
        match_buyer_role("VP Marketing", "Engineer", "Marketing", None)[0]
        == BuyerRoleMatch.PARTIAL
    )
    assert (
        match_buyer_role("VP Marketing", "Engineer", "Engineering", "Senior")[0]
        == BuyerRoleMatch.UNKNOWN
    )
    assert match_buyer_role("VP Marketing", "VP Marketing", None, None) == match_buyer_role(
        "VP Marketing", "VP Marketing", None, None
    )


def test_discovery_persists_safe_contacts_usage_audit_and_role_matches(session):
    case = seed_case(session)
    provider = FakeContactProvider(
        contacts=(
            ProviderContact(
                reference="p1",
                first_name="Ava",
                last_name="One",
                job_title="VP Marketing",
                email="AVA@EXAMPLE.TEST",
                confidence=95,
            ),
            ProviderContact(
                reference="p2",
                first_name="Ben",
                last_name="Two",
                job_title="VP Marketing EMEA",
                confidence=80,
            ),
            ProviderContact(
                reference="p3",
                first_name="Cam",
                last_name="Three",
                job_title="Engineer",
                department="Engineering",
            ),
        )
    )
    service = service_for(session, case, provider)

    contacts = service.discover(case["relationship"].id, case["qualification"].id)

    assert provider.calls["search"] == 1
    assert len(contacts) == 3
    assert {x.buyer_role_match for x in contacts} == {
        BuyerRoleMatch.EXACT,
        BuyerRoleMatch.STRONG,
        BuyerRoleMatch.UNKNOWN,
    }
    assert all(x.classification == DataClassification.THIRD_PARTY_LICENSED for x in contacts)
    assert all(x.redistribution_status == RedistributionStatus.RESTRICTED for x in contacts)
    assert session.scalar(select(ContactEmail).where(ContactEmail.email == "ava@example.test"))
    usage = session.scalar(
        select(ProviderUsage).where(ProviderUsage.operation == "CONTACT_SEARCH")
    )
    assert usage.success is True
    assert usage.credits_used == 2
    assert usage.credits_remaining == 8
    assert usage.request_identifier == "request-search"
    audit = session.scalar(select(AuditLog).where(AuditLog.action == "contact.discovery"))
    assert set(audit.metadata_json) == {
        "company_id",
        "qualification_id",
        "contact_id",
        "provider",
        "operation",
        "result_category",
        "buyer_role",
    }
    assert "ava@example.test" not in str(audit.metadata_json).lower()


def test_discovery_cache_force_refresh_and_duplicate_handling(session):
    case = seed_case(session)
    provider = FakeContactProvider(
        contacts=(
            ProviderContact(
                reference="same-provider-id",
                first_name="Ava",
                last_name="One",
                job_title="VP Marketing",
                email="ava@example.test",
            ),
        )
    )
    service = service_for(session, case, provider)

    service.discover(case["relationship"].id, case["qualification"].id)
    service.discover(case["relationship"].id, case["qualification"].id)
    assert provider.calls["search"] == 1
    assert session.query(ContactCandidate).count() == 1
    cached = session.scalars(
        select(ProviderUsage)
        .where(ProviderUsage.operation == "CONTACT_SEARCH")
        .order_by(ProviderUsage.id)
    ).all()
    assert len(cached) == 2
    assert any(x.cache_hit and x.credits_used == 0 for x in cached)

    service.discover(
        case["relationship"].id,
        case["qualification"].id,
        force_refresh=True,
    )
    assert provider.calls["search"] == 2
    assert session.query(ContactCandidate).count() == 1


def test_duplicate_fallback_uses_email_not_name(session):
    case = seed_case(session)
    provider = FakeContactProvider(
        contacts=(
            ProviderContact(
                first_name="Same",
                last_name="Name",
                job_title="VP Marketing",
                email="one@example.test",
            ),
            ProviderContact(
                first_name="Same",
                last_name="Name",
                job_title="VP Marketing",
                email="two@example.test",
            ),
        )
    )
    service = service_for(session, case, provider)
    service.discover(case["relationship"].id, case["qualification"].id)
    assert session.query(ContactCandidate).count() == 2

    provider.contacts = (
        ProviderContact(
            first_name="Different",
            last_name="Display",
            job_title="VP Marketing",
            email="ONE@EXAMPLE.TEST",
        ),
    )
    service.discover(
        case["relationship"].id,
        case["qualification"].id,
        force_refresh=True,
    )
    assert session.query(ContactCandidate).count() == 2


@pytest.mark.parametrize(
    "status",
    [QualificationStatus.NURTURE, QualificationStatus.NOT_QUALIFIED],
)
def test_discovery_blocks_ineligible_qualification_before_provider_call(session, status):
    case = seed_case(session, qualification_status=status)
    provider = FakeContactProvider()
    service = service_for(session, case, provider)
    with pytest.raises(ConflictError, match="NOT_QUALIFIED"):
        service.discover(case["relationship"].id, case["qualification"].id)
    assert provider.calls["search"] == 0


def test_discovery_blocks_stale_assessment_before_provider_call(session):
    case = seed_case(session)
    case["assessment"].evidence_snapshot[0]["selected_fact_id"] = "old-fact"
    provider = FakeContactProvider()
    service = service_for(session, case, provider)
    with pytest.raises(ConflictError, match="REQUALIFICATION_REQUIRED"):
        service.discover(case["relationship"].id, case["qualification"].id)
    assert provider.calls["search"] == 0


def test_tenant_isolation_and_read_only_credit_controls(session):
    case = seed_case(session, role=MembershipRole.READ_ONLY)
    provider = FakeContactProvider()
    service = service_for(session, case, provider)
    with pytest.raises(AuthorizationError):
        service.discover(case["relationship"].id, case["qualification"].id)
    assert provider.calls["search"] == 0

    other = Organization(name="Other", slug="other-" + uuid4().hex[:8])
    session.add(other)
    session.flush()
    wrong_tenant = TenantContext(other.id, case["user"].id, MembershipRole.MARKETING_USER)
    wrong_service = ContactDiscoveryService(session, wrong_tenant, ProviderRegistry([provider]), NOW)
    with pytest.raises(NotFoundError):
        wrong_service.list(case["relationship"].id)


def test_email_finder_persists_restricted_channel_and_caches(session):
    case = seed_case(session)
    provider = FakeContactProvider(
        contacts=(
            ProviderContact(
                reference="person-1",
                first_name="Ava",
                last_name="One",
                job_title="VP Marketing",
            ),
        ),
        found_email="FOUND@EXAMPLE.TEST",
    )
    service = service_for(session, case, provider)
    contact = service.discover(case["relationship"].id, case["qualification"].id)[0]

    service.find_email(case["relationship"].id, contact.id)
    service.find_email(case["relationship"].id, contact.id)

    assert provider.calls["find"] == 1
    channel = session.scalar(select(ContactEmail).where(ContactEmail.contact_id == contact.id))
    assert channel.email == "found@example.test"
    assert channel.classification == DataClassification.THIRD_PARTY_LICENSED
    assert channel.redistribution_status == RedistributionStatus.RESTRICTED
    usages = session.scalars(
        select(ProviderUsage).where(ProviderUsage.operation == "EMAIL_FIND")
    ).all()
    assert len(usages) == 2
    assert any(x.cache_hit and x.credits_used == 0 for x in usages)


def test_email_finder_requires_names_without_calling_provider(session):
    case = seed_case(session)
    provider = FakeContactProvider(
        contacts=(ProviderContact(reference="person-1", job_title="VP Marketing"),)
    )
    service = service_for(session, case, provider)
    contact = service.discover(case["relationship"].id, case["qualification"].id)[0]
    with pytest.raises(ConflictError, match="NAME_REQUIRED"):
        service.find_email(case["relationship"].id, contact.id)
    assert provider.calls["find"] == 0


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("valid", EmailVerificationStatus.VALID),
        ("invalid", EmailVerificationStatus.INVALID),
        ("accept_all", EmailVerificationStatus.ACCEPT_ALL),
        ("risky", EmailVerificationStatus.RISKY),
        ("mystery", EmailVerificationStatus.UNVERIFIABLE),
    ],
)
def test_email_verification_mapping_and_cache(session, raw, expected):
    case = seed_case(session)
    provider = FakeContactProvider(
        contacts=(
            ProviderContact(
                reference="person-1",
                first_name="Ava",
                last_name="One",
                job_title="VP Marketing",
                email="ava@example.test",
            ),
        ),
        verification=raw,
    )
    service = service_for(session, case, provider)
    contact = service.discover(case["relationship"].id, case["qualification"].id)[0]

    service.verify_email(case["relationship"].id, contact.id)
    service.verify_email(case["relationship"].id, contact.id)

    assert provider.calls["verify"] == 1
    channel = session.scalar(select(ContactEmail).where(ContactEmail.contact_id == contact.id))
    assert channel.verification_status == expected
    assert channel.verification_score == 87
    assert channel.verified_at == NOW
    usages = session.scalars(
        select(ProviderUsage).where(ProviderUsage.operation == "EMAIL_VERIFY")
    ).all()
    assert len(usages) == 2
    assert any(x.cache_hit and x.credits_used == 0 for x in usages)


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (ProviderNotConfigured("missing"), "NOT_CONFIGURED"),
        (ProviderRateLimited("limit"), "RATE_LIMITED"),
        (ProviderAuthenticationError("auth"), "AUTHENTICATION_ERROR"),
        (ProviderMalformedResponse("bad"), "MALFORMED_RESPONSE"),
        (ProviderError("failure"), "PROVIDER_ERROR"),
    ],
)
def test_provider_failures_are_sanitized_in_usage_without_contacts(session, error, category):
    case = seed_case(session)
    provider = FakeContactProvider(error=error)
    service = service_for(session, case, provider)
    with pytest.raises(type(error)):
        service.discover(case["relationship"].id, case["qualification"].id)
    usage = session.scalar(
        select(ProviderUsage).where(ProviderUsage.operation == "CONTACT_SEARCH")
    )
    assert usage.response_status == "FAILURE"
    assert usage.error_category == category
    assert usage.success is False
    assert session.query(ContactCandidate).count() == 0
    serialized = str(usage.__dict__)
    assert "api_key" not in serialized.lower()


def test_unknown_credit_counts_remain_unknown(session):
    case = seed_case(session)
    provider = FakeContactProvider(
        contacts=(ProviderContact(reference="p1", job_title="VP Marketing"),),
        credits_used=None,
        credits_remaining=None,
    )
    service = service_for(session, case, provider)
    service.discover(case["relationship"].id, case["qualification"].id)
    usage = session.scalar(
        select(ProviderUsage).where(ProviderUsage.operation == "CONTACT_SEARCH")
    )
    assert usage.credits_used is None
    assert usage.credits_remaining is None


def test_hunter_advertises_contact_capabilities_without_calling_live_api(monkeypatch):
    monkeypatch.delenv("HUNTER_API_KEY", raising=False)
    provider = HunterProvider(api_key=None)
    assert ProviderCapability.SEARCH_CONTACTS in provider.capabilities
    assert ProviderCapability.FIND_EMAIL in provider.capabilities
    assert ProviderCapability.VERIFY_EMAIL in provider.capabilities
    assert not provider.configured
    with pytest.raises(ProviderNotConfigured):
        provider.search_contacts("example.test", 1)


def test_hunter_maps_search_finder_and_verifier_without_raw_payload_persistence():
    def handler(request: httpx.Request):
        path = request.url.path
        if path.endswith("/domain-search"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "emails": [
                            {
                                "id": 7,
                                "first_name": "Ava",
                                "last_name": "One",
                                "position": "VP Marketing",
                                "value": "ava@example.test",
                                "confidence": 93,
                            }
                        ],
                        "SECRET_RAW_PROVIDER_VALUE": "must-not-map",
                    },
                    "meta": {"params": {"credits_used": 1}},
                },
                headers={"x-request-id": "safe-request"},
                request=request,
            )
        if path.endswith("/email-finder"):
            return httpx.Response(
                200,
                json={"data": {"email": "found@example.test", "confidence": 88}},
                request=request,
            )
        return httpx.Response(
            200,
            json={"data": {"status": "valid", "score": 99}},
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = HunterProvider(api_key="test-secret", client=client)
    search = provider.search_contacts("example.test", 10)
    found = provider.find_email(
        {"domain": "example.test", "first_name": "Ava", "last_name": "One"}
    )
    verified = provider.verify_email("found@example.test")

    assert search.contacts[0].reference == "7"
    assert search.contacts[0].job_title == "VP Marketing"
    assert search.credits_used == 1
    assert found.contacts[0].email == "found@example.test"
    assert verified.contacts[0].reference == "valid"
    assert "SECRET_RAW_PROVIDER_VALUE" not in repr(search)


@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [
        (401, ProviderAuthenticationError),
        (403, ProviderAuthenticationError),
        (429, ProviderRateLimited),
        (500, ProviderError),
    ],
)
def test_hunter_maps_http_failures_without_exposing_api_key(status_code, error_type):
    def handler(request: httpx.Request):
        return httpx.Response(status_code, json={"errors": []}, request=request)

    provider = HunterProvider(
        api_key="super-secret-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(error_type) as caught:
        provider.search_contacts("example.test", 1)
    assert "super-secret-key" not in str(caught.value)


def test_hunter_rejects_invalid_json_and_malformed_data():
    invalid = HunterProvider(
        api_key="secret",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=b"not-json", request=request)
            )
        ),
    )
    with pytest.raises(ProviderMalformedResponse):
        invalid.search_contacts("example.test", 1)

    malformed = HunterProvider(
        api_key="secret",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, json={"data": "wrong-shape"}, request=request
                )
            )
        ),
    )
    with pytest.raises(ProviderMalformedResponse):
        malformed.search_contacts("example.test", 1)
