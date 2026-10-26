import pytest
from sqlalchemy import func, select

from marketingiq.application.auth import authenticate
from marketingiq.domain.models import (
    AuditLog,
    MembershipRole,
    Organization,
    OrganizationMembership,
    User,
)
from marketingiq.jobs.create_admin import bootstrap_admin

PASSWORD = "a-long-admin-password"


def test_bootstrap_creates_org_user_and_admin_membership(session):
    result = bootstrap_admin(
        session,
        email=" Owner@Example.com ",
        password=PASSWORD,
        org_name="Barmageyat",
        org_slug="barmageyat",
    )

    assert result.organization_created and result.user_created and result.membership_created
    user = session.get(User, result.user_id)
    assert user.email == "owner@example.com"
    assert authenticate(session, "owner@example.com", PASSWORD) is not None
    membership = session.scalar(select(OrganizationMembership))
    assert membership.role == MembershipRole.ORGANIZATION_ADMIN
    assert membership.organization_id == result.organization_id
    assert session.scalar(select(func.count()).select_from(AuditLog)) == 1


def test_bootstrap_is_idempotent_and_keeps_password_unless_reset(session):
    first = bootstrap_admin(
        session,
        email="owner@example.com",
        password=PASSWORD,
        org_name="Barmageyat",
        org_slug="barmageyat",
    )
    second = bootstrap_admin(
        session,
        email="owner@example.com",
        password=None,
        org_name="Ignored Name",
        org_slug="barmageyat",
    )

    assert second.organization_id == first.organization_id
    assert not second.organization_created
    assert not second.user_created
    assert not second.membership_created
    assert not second.password_updated
    assert session.scalar(select(func.count()).select_from(Organization)) == 1
    assert authenticate(session, "owner@example.com", PASSWORD) is not None

    third = bootstrap_admin(
        session,
        email="owner@example.com",
        password="a-brand-new-password",
        org_name="Barmageyat",
        org_slug="barmageyat",
        reset_password=True,
    )
    assert third.password_updated
    assert authenticate(session, "owner@example.com", PASSWORD) is None
    assert authenticate(session, "owner@example.com", "a-brand-new-password") is not None


def test_bootstrap_promotes_existing_member_to_admin(session):
    bootstrap_admin(
        session,
        email="owner@example.com",
        password=PASSWORD,
        org_name="Barmageyat",
        org_slug="barmageyat",
    )
    membership = session.scalar(select(OrganizationMembership))
    membership.role = MembershipRole.READ_ONLY
    session.commit()

    bootstrap_admin(
        session,
        email="owner@example.com",
        password=None,
        org_name="Barmageyat",
        org_slug="barmageyat",
    )

    assert session.scalar(select(OrganizationMembership)).role == (
        MembershipRole.ORGANIZATION_ADMIN
    )


@pytest.mark.parametrize(
    ("email", "password", "slug", "message"),
    [
        ("not-an-email", PASSWORD, "barmageyat", "valid email"),
        ("owner@example.com", "short", "barmageyat", "at least 12"),
        ("owner@example.com", PASSWORD, "Bad Slug", "slug"),
    ],
)
def test_bootstrap_rejects_invalid_input(session, email, password, slug, message):
    with pytest.raises(ValueError, match=message):
        bootstrap_admin(
            session,
            email=email,
            password=password,
            org_name="Barmageyat",
            org_slug=slug,
        )
