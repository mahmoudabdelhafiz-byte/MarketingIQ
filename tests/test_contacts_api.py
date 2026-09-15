from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from marketingiq.api.app import create_app
from marketingiq.application.auth import hash_password
from marketingiq.application.qualification import LeadQualificationService
from marketingiq.application.research import ProviderRegistry
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.models import (
    ICP,
    Base,
    Company,
    CompanyFact,
    CompanyIdentifier,
    ContactCandidate,
    DataClassification,
    FitGrade,
    FitStatus,
    MembershipRole,
    Organization,
    OrganizationCompany,
    OrganizationMembership,
    Product,
    ProductFitAssessment,
    ProviderUsage,
    RedistributionStatus,
    User,
)
from marketingiq.domain.providers import (
    ContactProviderResult,
    ProviderCapability,
    ProviderContact,
    ProviderRateLimited,
)

SECRET = "a-secure-contact-api-secret-that-is-long-enough"
PASSWORD = "correct horse battery"


class ApiFakeProvider:
    key = "HUNTER"
    capabilities = frozenset(
        {
            ProviderCapability.SEARCH_CONTACTS,
            ProviderCapability.FIND_EMAIL,
            ProviderCapability.VERIFY_EMAIL,
        }
    )
    costs_credits = True

    def __init__(self, error=None):
        self.error = error
        self.calls = {"search": 0, "find": 0, "verify": 0}

    @property
    def configured(self):
        return True

    def search_contacts(self, company_identifier, max_results):
        self.calls["search"] += 1
        if self.error:
            raise self.error
        return ContactProviderResult(
            self.key,
            (
                ProviderContact(
                    reference="api-person-1",
                    first_name="Ava",
                    last_name="One",
                    job_title="VP Marketing",
                ),
            )[:max_results],
            "api-request-search",
            1,
            None,
        )

    def find_email(self, person):
        self.calls["find"] += 1
        if self.error:
            raise self.error
        return ContactProviderResult(
            self.key,
            (
                ProviderContact(
                    reference="api-person-1",
                    first_name=person.get("first_name"),
                    last_name=person.get("last_name"),
                    email="ava@example.test",
                    confidence=92,
                ),
            ),
            "api-request-find",
            1,
            None,
        )

    def verify_email(self, email):
        self.calls["verify"] += 1
        if self.error:
            raise self.error
        return ContactProviderResult(
            self.key,
            (ProviderContact(reference="valid", email=email, confidence=97),),
            "api-request-verify",
            1,
            None,
        )


def seed_api_database(url):
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    now = datetime.now(UTC)
    suffix = uuid4().hex[:10]
    with Session(engine) as session:
        org = Organization(name="Contact API", slug="contact-api-" + suffix)
        marketing = User(
            email="marketing-" + suffix + "@example.test",
            password_hash=hash_password(PASSWORD),
        )
        reader = User(
            email="reader-" + suffix + "@example.test",
            password_hash=hash_password(PASSWORD),
        )
        company = Company(canonical_name="Target")
        product = Product(
            organization=org,
            name="Product",
            slug="product-" + suffix,
            primary_buyer_roles=["VP Marketing"],
            secondary_buyer_roles=[],
        )
        relationship = OrganizationCompany(organization=org, company=company)
        session.add_all([marketing, reader, product, relationship])
        session.flush()
        session.add_all(
            [
                OrganizationMembership(
                    organization_id=org.id,
                    user_id=marketing.id,
                    role=MembershipRole.MARKETING_USER,
                ),
                OrganizationMembership(
                    organization_id=org.id,
                    user_id=reader.id,
                    role=MembershipRole.READ_ONLY,
                ),
            ]
        )
        icp = ICP(
            organization_id=org.id,
            product=product,
            name="ICP",
            version=1,
            is_active=True,
        )
        session.add(icp)
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
            observed_at=now,
        )
        session.add(fact)
        session.flush()
        criterion = {
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
            evaluated_at=now,
            workflow_version="test",
            evidence_snapshot=[criterion],
            explanation={"criteria": [criterion]},
            classification=DataClassification.MARKETINGIQ_DERIVED,
            created_by_user_id=marketing.id,
        )
        session.add(assessment)
        session.flush()
        qualification = LeadQualificationService(
            session,
            TenantContext(org.id, marketing.id, MembershipRole.ORGANIZATION_ADMIN),
            now,
        ).execute(relationship.id, assessment.id)
        session.commit()
        return {
            "org_id": org.id,
            "relationship_id": relationship.id,
            "qualification_id": qualification.id,
            "marketing_email": marketing.email,
            "reader_email": reader.email,
        }


def login(client, email):
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": PASSWORD},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_contact_api_enforces_limits_rbac_and_runs_full_channel_flow(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'contacts-api.db'}"
    seeded = seed_api_database(url)
    provider = ApiFakeProvider()
    app = create_app(url, SECRET)
    app.state.provider_registry = ProviderRegistry([provider])
    client = TestClient(app)
    marketing = login(client, seeded["marketing_email"])
    reader = login(client, seeded["reader_email"])
    base = (
        f"/api/v1/organizations/{seeded['org_id']}/companies/"
        f"{seeded['relationship_id']}/contacts"
    )

    denied = client.post(
        f"{base}/discover",
        headers=reader,
        json={"qualification_id": seeded["qualification_id"]},
    )
    assert denied.status_code == 403
    assert provider.calls["search"] == 0

    too_many = client.post(
        f"{base}/discover",
        headers=marketing,
        json={"qualification_id": seeded["qualification_id"], "max_results": 26},
    )
    assert too_many.status_code == 422
    assert provider.calls["search"] == 0

    discovered = client.post(
        f"{base}/discover",
        headers=marketing,
        json={"qualification_id": seeded["qualification_id"], "max_results": 25},
    )
    assert discovered.status_code == 201
    assert provider.calls["search"] == 1
    contact_id = discovered.json()[0]["id"]

    listing = client.get(base, headers=reader)
    detail = client.get(f"{base}/{contact_id}", headers=reader)
    assert listing.status_code == 200
    assert detail.status_code == 200

    assert (
        client.post(
            f"{base}/{contact_id}/find-email",
            headers=reader,
            json={},
        ).status_code
        == 403
    )
    found = client.post(
        f"{base}/{contact_id}/find-email",
        headers=marketing,
        json={},
    )
    assert found.status_code == 200
    assert provider.calls["find"] == 1

    verified = client.post(
        f"{base}/{contact_id}/verify-email",
        headers=marketing,
        json={},
    )
    assert verified.status_code == 200
    assert verified.json()["emails"][0]["verification_status"] == "VALID"
    assert provider.calls["verify"] == 1


def test_provider_failure_usage_survives_api_request_rollback(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'contacts-failure.db'}"
    seeded = seed_api_database(url)
    provider = ApiFakeProvider(error=ProviderRateLimited("sensitive provider detail"))
    app = create_app(url, SECRET)
    app.state.provider_registry = ProviderRegistry([provider])
    client = TestClient(app, raise_server_exceptions=False)
    marketing = login(client, seeded["marketing_email"])
    base = (
        f"/api/v1/organizations/{seeded['org_id']}/companies/"
        f"{seeded['relationship_id']}/contacts"
    )

    response = client.post(
        f"{base}/discover",
        headers=marketing,
        json={"qualification_id": seeded["qualification_id"]},
    )
    assert response.status_code == 500
    assert "sensitive provider detail" not in response.text

    engine = create_engine(url)
    with Session(engine) as session:
        usage = session.scalar(
            select(ProviderUsage).where(ProviderUsage.operation == "CONTACT_SEARCH")
        )
        assert usage is not None
        assert usage.success is False
        assert usage.response_status == "FAILURE"
        assert usage.error_category == "RATE_LIMITED"
        assert usage.completed_at is not None
        assert session.scalar(select(ContactCandidate)) is None
