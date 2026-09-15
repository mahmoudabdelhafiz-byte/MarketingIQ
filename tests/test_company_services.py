from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from marketingiq.application.companies import (
    MAX_CSV_BYTES,
    MAX_CSV_ROWS,
    CompanyService,
    EvidenceInput,
    FactInput,
    normalize_domain,
)
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.models import (
    AuditLog,
    Company,
    CompanyFact,
    DataClassification,
    MembershipRole,
    Organization,
    OrganizationCompany,
    OrganizationMembership,
    RedistributionStatus,
    User,
)


def make_tenant(session, slug: str, role=MembershipRole.ORGANIZATION_ADMIN):
    organization = Organization(name=slug.title(), slug=slug)
    user = User(email=f"{slug}@example.test", password_hash="not-a-real-hash")
    session.add_all([organization, user])
    session.flush()
    session.add(OrganizationMembership(organization_id=organization.id, user_id=user.id, role=role))
    session.flush()
    return organization, user, CompanyService(session, TenantContext(organization.id, user.id))


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (" Example.COM ", "example.com"),
        ("https://www.Example.com/", "example.com"),
        ("www.example.co.uk", "example.co.uk"),
    ],
)
def test_domain_normalization(source, expected):
    assert normalize_domain(source) == expected


@pytest.mark.parametrize(
    "source", ["", "localhost", "127.0.0.1", "https://example.com/path", "example.com?q=1"]
)
def test_invalid_domains_are_rejected(source):
    with pytest.raises(ValueError):
        normalize_domain(source)


def test_two_tenants_reuse_company_but_private_relationships_are_isolated(session):
    first_org, _, first = make_tenant(session, "first")
    second_org, _, second = make_tenant(session, "second")
    first_relationship = first.attach_company(
        domain="HTTPS://WWW.Example.com/",
        canonical_name="Example",
        lifecycle_status="NEW",
        private_notes="first only",
    )
    second_relationship = second.attach_company(
        domain="example.com",
        canonical_name="Do not overwrite",
        lifecycle_status="QUALIFIED",
        private_notes="second only",
    )

    assert first_relationship.company_id == second_relationship.company_id
    assert session.scalar(select(func.count()).select_from(Company)) == 1
    assert first.list_companies()[0].private_notes == "first only"
    assert second.list_companies()[0].private_notes == "second only"
    with pytest.raises(LookupError):
        first.get_company(second_relationship.id)
    with pytest.raises(LookupError):
        first.update_relationship(
            second_relationship.id, lifecycle_status="BAD", private_notes="leak"
        )
    assert first_relationship.organization_id == first_org.id
    assert second_relationship.organization_id == second_org.id


def test_read_only_cannot_write_and_marketing_user_can_attach_but_not_import(session):
    _, _, reader = make_tenant(session, "reader", MembershipRole.READ_ONLY)
    _, _, marketer = make_tenant(session, "marketer", MembershipRole.MARKETING_USER)
    with pytest.raises(PermissionError):
        reader.attach_company(domain="reader.test", canonical_name="Reader")
    relationship = marketer.attach_company(domain="marketer.test", canonical_name="Marketer")
    assert relationship.company.canonical_name == "Marketer"
    with pytest.raises(PermissionError):
        marketer.preview_csv("company_name,domain\nExample,example.com\n")


def test_facts_are_append_only_provenanced_and_tenant_scoped(session):
    first_org, _, first = make_tenant(session, "fact-first")
    _, _, second = make_tenant(session, "fact-second")
    first_relationship = first.attach_company(domain="facts.test", canonical_name="Facts")
    second_relationship = second.attach_company(domain="facts.test", canonical_name="Facts")
    source = first.get_or_create_source("MANUAL", "Manual")
    january = first.add_fact(
        first_relationship.id,
        FactInput(
            fact_key="employee_range",
            value={"min": 100, "max": 250},
            classification=DataClassification.CUSTOMER_PROVIDED,
            confidence=90,
            observed_at=datetime(2026, 1, 1, tzinfo=UTC),
            evidence=[EvidenceInput(data_source_id=source.id, reference_text="Customer file")],
        ),
    )
    september = first.add_fact(
        first_relationship.id,
        FactInput(
            fact_key="employee_range",
            value={"min": 250, "max": 500},
            classification=DataClassification.CUSTOMER_PROVIDED,
            confidence=80,
            observed_at=datetime(2026, 9, 1, tzinfo=UTC),
        ),
    )
    session.add(
        CompanyFact(
            company_id=first_relationship.company_id,
            organization_id=None,
            fact_key="country",
            value="SA",
            classification=DataClassification.PUBLIC_EVIDENCE,
            redistribution_status=RedistributionStatus.ALLOWED,
            confidence=70,
        )
    )
    session.flush()

    assert [fact.id for fact in first.list_facts(first_relationship.id)][:2] == [
        january.id,
        september.id,
    ]
    second_facts = second.list_facts(second_relationship.id)
    assert all(fact.organization_id != first_org.id for fact in second_facts)
    assert [fact.fact_key for fact in second_facts] == ["country"]
    assert january.evidences[0].data_source_id == source.id
    assert january.redistribution_status == RedistributionStatus.UNKNOWN
    assert session.scalar(select(func.count()).select_from(CompanyFact)) == 3


def test_provenance_and_redistribution_rules(session):
    _, _, service = make_tenant(session, "provenance")
    relationship = service.attach_company(domain="provenance.test", canonical_name="Provenance")
    with pytest.raises(ValueError, match="data-source provenance"):
        service.add_fact(
            relationship.id,
            FactInput(
                fact_key="industry",
                value="Technology",
                classification=DataClassification.PUBLIC_EVIDENCE,
                confidence=80,
            ),
        )
    source = service.get_or_create_source("MANUAL", "Manual")
    with pytest.raises(ValueError, match="cannot default to ALLOWED"):
        service.add_fact(
            relationship.id,
            FactInput(
                fact_key="industry",
                value="Technology",
                classification=DataClassification.THIRD_PARTY_LICENSED,
                redistribution_status=RedistributionStatus.ALLOWED,
                confidence=80,
                evidence=[EvidenceInput(data_source_id=source.id)],
            ),
        )
    with pytest.raises(ValueError, match="absolute HTTP"):
        service.add_fact(
            relationship.id,
            FactInput(
                fact_key="industry",
                value="Technology",
                classification=DataClassification.PUBLIC_EVIDENCE,
                confidence=80,
                evidence=[EvidenceInput(data_source_id=source.id, reference_url="file:///tmp/x")],
            ),
        )


def test_csv_preview_is_non_mutating_and_import_handles_duplicates(session):
    _, _, service = make_tenant(session, "importer")
    other_org, _, other = make_tenant(session, "other-importer")
    existing = other.attach_company(domain="shared.test", canonical_name="Original")
    csv_text = (
        "company_name,domain,country_code,employee_min,employee_max,lifecycle_status,private_notes\n"
        "Shared,https://www.shared.test,SA,10,20,NEW,tenant note\n"
        "New,new.test,AE,20,50,NEW,new note\n"
        "Duplicate,www.new.test,AE,20,50,NEW,ignored\n"
    )
    before_relationships = session.scalar(select(func.count()).select_from(OrganizationCompany))
    before_companies = session.scalar(select(func.count()).select_from(Company))
    preview = service.preview_csv(csv_text)
    assert [row.action for row in preview.rows] == [
        "REUSE_GLOBAL_AND_ATTACH",
        "CREATE_GLOBAL_AND_ATTACH",
        "ALREADY_ATTACHED",
    ]
    assert (
        session.scalar(select(func.count()).select_from(OrganizationCompany))
        == before_relationships
    )
    assert session.scalar(select(func.count()).select_from(Company)) == before_companies

    report = service.import_csv(csv_text)
    assert report.valid_rows == 3
    assert session.scalar(select(func.count()).select_from(Company)) == before_companies + 1
    assert service.list_companies()[0].company_id == existing.company_id
    assert service.list_companies()[0].private_notes == "tenant note"
    assert existing.company.canonical_name == "Original"
    audit = session.scalars(select(AuditLog).where(AuditLog.action == "company.csv_imported")).one()
    assert audit.metadata_json == {"total_rows": 3, "new_companies": 1}
    assert audit.organization_id != other_org.id


def test_invalid_and_oversized_csv_is_rejected_without_partial_import(session):
    _, _, service = make_tenant(session, "invalid-import")
    report = service.preview_csv(
        "company_name,domain,employee_min\nMissing,,5\nBad,bad.test,nope\n"
    )
    assert report.invalid_rows == 2
    assert report.rows[0].action == "REJECT"
    with pytest.raises(ValueError, match="invalid rows"):
        service.import_csv("company_name,domain\nMissing,\n")
    with pytest.raises(ValueError, match="byte limit"):
        service.preview_csv(b"x" * (MAX_CSV_BYTES + 1))
    oversized = "company_name,domain\n" + "\n".join(
        f"Company {index},company-{index}.test" for index in range(MAX_CSV_ROWS + 1)
    )
    with pytest.raises(ValueError, match="row limit"):
        service.preview_csv(oversized)
    assert session.scalar(select(func.count()).select_from(Company)) == 0
