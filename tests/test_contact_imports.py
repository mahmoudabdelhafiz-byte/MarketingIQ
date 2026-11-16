"""Importing people from your own lists and adding them to decision-maker discovery."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from test_contacts import NOW, seed_case
from test_decision_makers import by_name, make_service, setup_personas

from marketingiq.application.contact_imports import (
    ContactImportService,
    parse_rows,
    pending_imported_count,
)
from marketingiq.application.errors import AuthorizationError
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.contact_imports import ImportedContact, ImportedContactStatus
from marketingiq.domain.models import (
    Company,
    ContactCandidate,
    DataClassification,
    MembershipRole,
    OrganizationCompany,
)
from marketingiq.domain.outbound import SuppressionEntry, SuppressionSource


def importer(session, case, role=MembershipRole.ORGANIZATION_ADMIN):
    # Bulk imports are an admin action, like the company CSV import.
    tenant = TenantContext(case["org"].id, case["user"].id, role)
    return ContactImportService(session, tenant, now=NOW)


def domain_of(case):
    return case["company"].identifiers[0].normalized_value


def test_parse_accepts_common_headers_and_reports_problems():
    rows = parse_rows(
        "Company;Website;Full Name;Job Title;Work Email;LinkedIn URL\n"
        "Acme;https://www.acme.test/about;Sara Ali;CIO;Sara@Acme.test;\n"
        ";;Omar;CTO;omar@gmail.com;\n"
        "Port Co;port.test;Lina Saad;COO;not-an-email;\n"
        ";;Nadia Ali;CEO;nadia@port.test;\n"
    )
    first, gmail, bad, derived = rows
    assert (first.domain, first.email, first.first_name, first.last_name) == (
        "acme.test",
        "sara@acme.test",
        "Sara",
        "Ali",
    )
    assert gmail.error and bad.error == "Email address is not valid"
    assert (derived.domain, derived.company) == ("port.test", "Port")


def test_parse_requires_email_or_name_column():
    with pytest.raises(ValueError, match="email column or a name column"):
        parse_rows("company,website\nAcme,acme.test\n")


def test_import_creates_companies_pending_people_and_skips_duplicates(session):
    case = seed_case(session)
    session.add(
        SuppressionEntry(
            organization_id=case["org"].id,
            email_normalized="blocked@newco.test",
            source=SuppressionSource.MANUAL,
            reason="Asked not to be contacted",
        )
    )
    session.flush()
    csv_text = (
        "company,website,name,title,email\n"
        "New Co,newco.test,Huda Nasser,IT Director,huda@newco.test\n"
        "New Co,newco.test,Blocked Person,CIO,blocked@newco.test\n"
        "New Co,newco.test,Huda Nasser,IT Director,huda@newco.test\n"
        "Bad,,,,\n"
    )
    svc = importer(session, case)
    preview = svc.preview(csv_text)
    assert (preview["imported"], preview["duplicates"], preview["new_companies"]) == (2, 1, 1)
    assert session.scalar(select(ImportedContact)) is None  # preview writes nothing

    result = svc.import_csv(csv_text, list_name="Expo leads")
    assert (result["imported"], result["suppressed"], result["duplicates"]) == (1, 1, 1)
    assert result["invalid_count"] == 1
    company = session.scalar(select(Company).where(Company.canonical_name == "New Co"))
    rel = session.scalar(
        select(OrganizationCompany).where(OrganizationCompany.company_id == company.id)
    )
    assert result["relationship_ids"] == [rel.id]
    statuses = {row.email: row.status for row in session.scalars(select(ImportedContact))}
    assert statuses == {
        "huda@newco.test": ImportedContactStatus.PENDING,
        "blocked@newco.test": ImportedContactStatus.SUPPRESSED,
    }
    assert pending_imported_count(session, rel.id) == 1

    again = svc.import_csv(csv_text, list_name="Expo leads")
    assert again["imported"] == 0 and again["duplicates"] == 3
    assert svc.lists()[0]["total"] == 2


@pytest.mark.parametrize("role", [MembershipRole.READ_ONLY, MembershipRole.MARKETING_USER])
def test_only_admins_import(session, role):
    case = seed_case(session)
    with pytest.raises(AuthorizationError):
        importer(session, case, role).import_csv("name,email\nA B,a@x.test\n", list_name="x")


def test_discovery_adds_imported_people_with_their_own_evidence(session):
    case = seed_case(session)
    setup_personas(session, case)
    domain = domain_of(case)
    importer(session, case).import_csv(
        "name,title,email,linkedin\n"
        f"Nadia Saleh,CTO,nadia@{domain},https://www.linkedin.com/in/nadia-s/\n"
        f"Sara Ahmed,Chief Information Officer,sara@{domain},\n",
        list_name="Partner referrals",
    )

    summary = make_service(session, case).discover(case["relationship"].id)

    nadia = by_name(summary, "Nadia Saleh")
    assert nadia["email"] == f"nadia@{domain}"
    assert nadia["email_state"] == "UNVERIFIED"
    assert nadia["role_match"] == "HIGH"
    # One uncorroborated source: a person must approve (or the email must verify) first.
    assert nadia["contact_status"] == "INSUFFICIENT_EVIDENCE"
    contact = session.get(ContactCandidate, nadia["id"])
    assert contact.classification == DataClassification.CUSTOMER_PROVIDED
    # Sara is also on the company website: merged into one person, now with an email.
    sara = by_name(summary, "Sara Ahmed")
    assert sara["email"] == f"sara@{domain}"
    assert sara["employment_status"] == "CURRENT_CONFIRMED"
    assert len([c for c in summary["contacts"] if c["full_name"] == "Sara Ahmed"]) == 1
    rows = list(session.scalars(select(ImportedContact)))
    assert {row.status for row in rows} == {ImportedContactStatus.ATTACHED}
    assert all(row.contact_id for row in rows)
    assert pending_imported_count(session, case["relationship"].id) == 0

    # Running again does not duplicate anyone.
    again = make_service(session, case).discover(case["relationship"].id)
    assert len(again["contacts"]) == len(summary["contacts"])


def test_trusted_list_is_pre_approved(session):
    case = seed_case(session)
    setup_personas(session, case)
    importer(session, case).import_csv(
        f"name,title,email\nNadia Saleh,CTO,nadia@{domain_of(case)}\n",
        list_name="Existing customers",
        trusted=True,
    )
    summary = make_service(session, case).discover(case["relationship"].id)
    nadia = by_name(summary, "Nadia Saleh")
    assert nadia["review_status"] == "APPROVED"
    assert nadia["contact_status"] == "EMAIL_UNVERIFIED"
    assert "Existing customers" in nadia["review_note"]
