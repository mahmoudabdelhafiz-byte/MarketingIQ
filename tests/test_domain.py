from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError, StatementError

from marketingiq.application.tenant import TenantContext, TenantRepository
from marketingiq.domain.models import (
    ICP,
    Company,
    CompanyFact,
    CompanyIdentifier,
    DataClassification,
    DataSource,
    Evidence,
    Organization,
    OrganizationCompany,
    Product,
    RedistributionStatus,
)


def test_tenant_repository_never_reads_or_writes_another_tenant(session):
    first = Organization(name="First", slug="first")
    second = Organization(name="Second", slug="second")
    session.add_all([first, second])
    session.flush()
    visible = Product(organization_id=first.id, name="Visible", slug="visible")
    hidden = Product(organization_id=second.id, name="Hidden", slug="hidden")
    session.add_all([visible, hidden])
    session.commit()

    repository = TenantRepository(session, TenantContext(first.id))
    assert session.scalars(repository.products_query()).all() == [visible]
    with pytest.raises(PermissionError):
        repository.add_product(Product(organization_id=second.id, name="Bad", slug="bad"))


def test_product_icp_and_global_company_tenant_relationship(session):
    organization = Organization(name="Tenant", slug="tenant")
    session.add(organization)
    session.flush()
    product = Product(organization=organization, name="Product", slug="product")
    icp = ICP(organization_id=organization.id, name="Enterprise", version=1)
    product.icps.append(icp)
    company = Company(canonical_name="Example")
    company.identifiers.append(
        CompanyIdentifier(kind="DOMAIN", value="Example.com", normalized_value="example.com")
    )
    relationship = OrganizationCompany(organization=organization, company=company)
    session.add_all([product, relationship])
    session.commit()

    assert product.icps == [icp]
    assert relationship.company.identifiers[0].normalized_value == "example.com"


def test_classification_and_confidence_constraints(session):
    company = Company(canonical_name="Example")
    session.add(company)
    session.flush()
    session.add(
        CompanyFact(
            company_id=company.id,
            fact_key="employee_range",
            value={"min": 501, "max": 1000},
            classification="NOT_A_CLASSIFICATION",
            redistribution_status=RedistributionStatus.RESTRICTED,
            confidence=90,
        )
    )
    with pytest.raises(StatementError):
        session.commit()
    session.rollback()

    session.add(
        CompanyFact(
            company_id=company.id,
            fact_key="employee_range",
            value={"min": 501, "max": 1000},
            classification=DataClassification.THIRD_PARTY_LICENSED,
            redistribution_status=RedistributionStatus.RESTRICTED,
            confidence=101,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_disagreeing_facts_and_evidence_are_preserved_as_history(session):
    company = Company(canonical_name="ABC Logistics")
    source = DataSource(provider_key="manual", display_name="Manual")
    first = CompanyFact(
        company_id="placeholder",
        fact_key="multiple_operating_locations",
        value=True,
        classification=DataClassification.PUBLIC_EVIDENCE,
        redistribution_status=RedistributionStatus.ALLOWED,
        confidence=95,
        observed_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    session.add_all([company, source])
    session.flush()
    first.company_id = company.id
    first.evidences.append(Evidence(source=source, reference_url="https://example.test/locations"))
    second = CompanyFact(
        company_id=company.id,
        fact_key="multiple_operating_locations",
        value=False,
        classification=DataClassification.PUBLIC_EVIDENCE,
        redistribution_status=RedistributionStatus.ALLOWED,
        confidence=70,
        observed_at=datetime(2026, 2, 1, tzinfo=UTC),
    )
    session.add_all([first, second])
    session.commit()

    assert first.id != second.id
    assert first.value is True and second.value is False
    assert first.evidences[0].reference_url.endswith("/locations")
