from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from marketingiq.application.discovery import (
    DISCOVERED_LIFECYCLE_STATUS,
    CompanyDiscoveryService,
    normalize_source_url,
)
from marketingiq.application.errors import AuthorizationError, ConflictError, NotFoundError
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.discovery import (
    CompanyDiscoveryCandidate,
    DiscoveredCompany,
    DiscoveryCandidateStatus,
    DiscoveryProviderError,
    DiscoveryProviderResult,
    DiscoveryRunStatus,
    DiscoverySkipReason,
)
from marketingiq.domain.models import (
    ICP,
    AuditLog,
    Company,
    CompanyIdentifier,
    ICPCriterion,
    MembershipRole,
    Organization,
    OrganizationCompany,
    Product,
    ProductStatus,
    User,
)

NOW = datetime(2026, 10, 25, 9, 0, tzinfo=UTC)


class FakeDiscoveryProvider:
    key = "FAKE_DISCOVERY"
    model = "fake-model"

    def __init__(self, companies=(), retrieved=(), *, fail=None, configured=True):
        self.companies = tuple(companies)
        self.retrieved = frozenset(retrieved)
        self.fail = fail
        self._configured = configured
        self.requests = []

    @property
    def configured(self):
        return self._configured

    def discover(self, request):
        self.requests.append(request)
        if self.fail:
            raise DiscoveryProviderError(self.fail)
        return DiscoveryProviderResult(
            provider_key=self.key,
            model=self.model,
            companies=self.companies,
            retrieved_urls=self.retrieved,
            searches_used=3,
            input_tokens=1200,
            output_tokens=300,
        )


def company(name, domain, *urls, country="sa", industry="Ports", rationale="Operates a port."):
    return DiscoveredCompany(
        name=name,
        domain=domain,
        source_urls=tuple(urls),
        country_code=country,
        industry=industry,
        rationale=rationale,
    )


def seed(session: Session, *, attached_domain=None, with_icp=True):
    suffix = uuid4().hex[:8]
    attached_domain = attached_domain or f"existing-{suffix}.example"
    organization = Organization(name="Discovery Tenant", slug="discovery-" + suffix)
    user = User(email=f"discovery-{suffix}@example.test", password_hash="x")
    product = Product(
        organization=organization,
        name="Truck Appointment System",
        slug="tas-" + suffix,
        description="Gate booking for container terminals",
        value_proposition="Cut gate queues with booked truck slots.",
        status=ProductStatus.ACTIVE,
    )
    existing = Company(canonical_name="Existing Terminal")
    existing.identifiers.append(
        CompanyIdentifier(
            kind="DOMAIN", value=attached_domain, normalized_value=attached_domain, is_primary=True
        )
    )
    relationship = OrganizationCompany(organization=organization, company=existing)
    session.add_all([user, product, relationship])
    session.flush()
    icp = None
    if with_icp:
        icp = ICP(
            organization_id=organization.id,
            product=product,
            name="Container terminals",
            version=1,
            is_active=True,
            employee_min=200,
        )
        icp.criteria = [
            ICPCriterion(kind="industry", value="Container terminal", weight=10, required=True),
            ICPCriterion(kind="country", value="SA", weight=5),
        ]
        session.add(icp)
        session.flush()
        icp.logical_id = icp.id
    session.flush()
    return {
        "organization": organization,
        "user": user,
        "product": product,
        "icp": icp,
        "attached_domain": attached_domain,
    }


def service(session, seeded, provider, *, role=MembershipRole.MARKETING_USER, limit=5, now=NOW):
    return CompanyDiscoveryService(
        session,
        TenantContext(seeded["organization"].id, seeded["user"].id, role),
        provider,
        now=now,
        daily_run_limit=limit,
    )


GROUNDED = "https://www.portalpha.example/about/"


def test_run_proposes_only_grounded_valid_new_companies(session):
    seeded = seed(session, attached_domain="existing.example")
    provider = FakeDiscoveryProvider(
        companies=[
            company("Port Alpha", "https://www.PortAlpha.example/", GROUNDED, "https://made.up/x"),
            company("Ghost Terminal", "ghost.example", "https://never-retrieved.example/"),
            company("Bad Domain", "not a domain", GROUNDED),
            company("Existing Terminal", "existing.example", GROUNDED),
            company("Port Alpha again", "portalpha.example", GROUNDED),
        ],
        retrieved=["https://portalpha.example/about", "https://made.up/other"],
    )

    run = service(session, seeded, provider).run(seeded["product"].id, max_results=10)

    assert run.status == DiscoveryRunStatus.COMPLETED
    assert (run.returned_count, run.proposed_count, run.skipped_count) == (5, 1, 4)
    assert (run.searches_used, run.input_tokens, run.output_tokens) == (3, 1200, 300)
    assert run.icp_id == seeded["icp"].id
    by_name = {c.company_name: c for c in run.candidates}
    alpha = by_name["Port Alpha"]
    assert alpha.status == DiscoveryCandidateStatus.PROPOSED
    assert alpha.normalized_domain == "portalpha.example"
    assert alpha.country_code == "SA"
    # Only the URL the search tool actually retrieved is kept as a source.
    assert alpha.source_urls == [GROUNDED]
    assert by_name["Ghost Terminal"].skip_reason == DiscoverySkipReason.UNGROUNDED
    assert by_name["Ghost Terminal"].source_urls == []
    assert by_name["Bad Domain"].skip_reason == DiscoverySkipReason.INVALID_DOMAIN
    assert by_name["Existing Terminal"].skip_reason == DiscoverySkipReason.ALREADY_ATTACHED
    assert by_name["Port Alpha again"].skip_reason == DiscoverySkipReason.DUPLICATE_IN_RUN

    # Discovery never touches the catalog by itself.
    assert session.scalar(
        select(CompanyIdentifier).where(CompanyIdentifier.normalized_value == "portalpha.example")
    ) is None

    request = provider.requests[0]
    assert request.product_name == "Truck Appointment System"
    assert request.criteria[0].kind == "industry" and request.criteria[0].required
    assert request.employee_min == 200
    assert "existing.example" in request.exclude_domains
    audit = session.scalar(select(AuditLog).where(AuditLog.action == "company_discovery.run"))
    assert audit.metadata_json["proposed"] == 1
    assert "portalpha.example" not in str(audit.metadata_json)


def test_max_results_truncates_provider_output(session):
    seeded = seed(session)
    provider = FakeDiscoveryProvider(
        companies=[company(f"Port {i}", f"port{i}.example", GROUNDED) for i in range(5)],
        retrieved=[GROUNDED],
    )
    run = service(session, seeded, provider).run(seeded["product"].id, max_results=2)
    assert run.returned_count == 2
    assert [c.normalized_domain for c in run.candidates] == ["port0.example", "port1.example"]
    with pytest.raises(ValueError):
        service(session, seeded, provider).run(seeded["product"].id, max_results=26)


def test_later_runs_exclude_proposed_and_rejected_domains(session):
    seeded = seed(session)
    provider = FakeDiscoveryProvider(
        companies=[
            company("Port Alpha", "portalpha.example", GROUNDED),
            company("Port Beta", "portbeta.example", GROUNDED),
        ],
        retrieved=[GROUNDED],
    )
    svc = service(session, seeded, provider)
    first = svc.run(seeded["product"].id)
    beta = next(c for c in first.candidates if c.normalized_domain == "portbeta.example")
    svc.reject(beta.id, note="Too small")

    second = svc.run(seeded["product"].id)

    excluded = provider.requests[1].exclude_domains
    assert {"portalpha.example", "portbeta.example", seeded["attached_domain"]} <= set(excluded)
    reasons = {c.normalized_domain: c.skip_reason for c in second.candidates}
    assert reasons == {
        "portalpha.example": DiscoverySkipReason.ALREADY_PROPOSED,
        "portbeta.example": DiscoverySkipReason.PREVIOUSLY_REJECTED,
    }
    assert second.proposed_count == 0


def test_accept_attaches_company_with_identity_only(session):
    seeded = seed(session)
    provider = FakeDiscoveryProvider(
        companies=[company("Port Alpha", "portalpha.example", GROUNDED, country="AE")],
        retrieved=[GROUNDED],
    )
    svc = service(session, seeded, provider)
    candidate = svc.run(seeded["product"].id).candidates[0]

    accepted = svc.accept(candidate.id, note="Known prospect")

    assert accepted.status == DiscoveryCandidateStatus.ACCEPTED
    assert accepted.reviewed_by_user_id == seeded["user"].id
    assert accepted.review_note == "Known prospect"
    relationship = session.get(OrganizationCompany, accepted.organization_company_id)
    assert relationship.organization_id == seeded["organization"].id
    assert relationship.lifecycle_status == DISCOVERED_LIFECYCLE_STATUS
    assert relationship.company.canonical_name == "Port Alpha"
    # AI-suggested attributes are not promoted into global company identity.
    assert relationship.company.country_code is None
    assert relationship.company.industry is None
    actions = set(session.scalars(select(AuditLog.action)))
    assert {"company.attached", "company_discovery.candidate_accepted"} <= actions

    with pytest.raises(ConflictError):
        svc.accept(candidate.id)
    with pytest.raises(ConflictError):
        svc.reject(candidate.id)


def test_accept_reuses_existing_global_company(session):
    seeded = seed(session)
    shared = Company(canonical_name="Port Alpha Global")
    shared.identifiers.append(
        CompanyIdentifier(
            kind="DOMAIN",
            value="portalpha.example",
            normalized_value="portalpha.example",
            is_primary=True,
        )
    )
    session.add(shared)
    session.flush()
    provider = FakeDiscoveryProvider(
        companies=[company("Port Alpha", "portalpha.example", GROUNDED)], retrieved=[GROUNDED]
    )
    svc = service(session, seeded, provider)
    candidate = svc.run(seeded["product"].id).candidates[0]
    accepted = svc.accept(candidate.id)
    relationship = session.get(OrganizationCompany, accepted.organization_company_id)
    assert relationship.company_id == shared.id


def test_provider_failure_is_persisted_without_candidates(session):
    seeded = seed(session)
    provider = FakeDiscoveryProvider(fail="RATE_LIMITED")
    run = service(session, seeded, provider).run(seeded["product"].id)
    assert run.status == DiscoveryRunStatus.FAILED
    assert run.error_category == "RATE_LIMITED"
    assert run.candidates == []
    assert session.scalar(select(CompanyDiscoveryCandidate)) is None


def test_preconditions_are_conflicts_before_any_spend(session):
    seeded = seed(session, with_icp=False)
    provider = FakeDiscoveryProvider()
    with pytest.raises(ConflictError, match="active ICP"):
        service(session, seeded, provider).run(seeded["product"].id)

    seeded = seed(session)
    unconfigured = FakeDiscoveryProvider(configured=False)
    with pytest.raises(ConflictError, match="not configured"):
        service(session, seeded, unconfigured).run(seeded["product"].id)

    seeded["product"].status = ProductStatus.ARCHIVED
    with pytest.raises(ConflictError, match="Archived"):
        service(session, seeded, provider).run(seeded["product"].id)
    assert provider.requests == [] and unconfigured.requests == []


def test_daily_run_limit_counts_only_today(session):
    seeded = seed(session)
    provider = FakeDiscoveryProvider()
    service(session, seeded, provider, limit=1, now=NOW - timedelta(days=1)).run(
        seeded["product"].id
    )
    svc = service(session, seeded, provider, limit=1)
    assert svc.status()["runs_remaining_today"] == 1
    svc.run(seeded["product"].id)
    assert svc.status()["runs_remaining_today"] == 0
    with pytest.raises(ConflictError, match="Daily"):
        svc.run(seeded["product"].id)
    assert len(provider.requests) == 2


def test_rbac_and_tenant_isolation(session):
    seeded = seed(session)
    other = seed(session)
    provider = FakeDiscoveryProvider(
        companies=[company("Port Alpha", "portalpha.example", GROUNDED)], retrieved=[GROUNDED]
    )
    candidate = service(session, seeded, provider).run(seeded["product"].id).candidates[0]

    reader = service(session, seeded, provider, role=MembershipRole.READ_ONLY)
    with pytest.raises(AuthorizationError):
        reader.run(seeded["product"].id)
    with pytest.raises(AuthorizationError):
        reader.accept(candidate.id)
    assert [c.id for c in reader.list_candidates()] == [candidate.id]

    outsider = service(session, other, provider)
    with pytest.raises(NotFoundError):
        outsider.run(seeded["product"].id)
    with pytest.raises(NotFoundError):
        outsider.accept(candidate.id)
    with pytest.raises(NotFoundError):
        outsider.get_run(candidate.run_id)
    assert outsider.list_candidates() == []
    assert outsider.list_runs() == []


def test_normalize_source_url():
    assert normalize_source_url("http://WWW.Example.com/a/") == "https://example.com/a"
    assert normalize_source_url("https://example.com/a#frag") == "https://example.com/a"
    assert normalize_source_url("ftp://example.com") is None
    assert normalize_source_url("https://user:pw@example.com/") is None
    assert normalize_source_url("not a url") is None
