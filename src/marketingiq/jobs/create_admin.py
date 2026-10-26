"""Create or update the first organization administrator.

MarketingIQ has no public registration. Operators bootstrap the first account from the hosting
shell after migrations have been applied::

    MARKETINGIQ_ADMIN_PASSWORD='...' python -m marketingiq.jobs.create_admin \
        --email owner@example.com --org-name "Barmageyat" --org-slug barmageyat

The password is read from ``MARKETINGIQ_ADMIN_PASSWORD`` or prompted interactively, so it never
appears in shell history or process listings. The command is idempotent: it reuses an existing
organization and user, and it only changes an existing password when ``--reset-password`` is set.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from marketingiq.application.auth import hash_password
from marketingiq.domain.models import (
    AuditLog,
    MembershipRole,
    Organization,
    OrganizationMembership,
    User,
)
from marketingiq.infrastructure.database import create_database_engine, create_session_factory

PASSWORD_ENV = "MARKETINGIQ_ADMIN_PASSWORD"
MIN_PASSWORD_LENGTH = 12
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class BootstrapResult:
    organization_id: str
    organization_created: bool
    user_id: str
    user_created: bool
    membership_created: bool
    password_updated: bool

    def as_dict(self) -> dict[str, object]:
        return self.__dict__.copy()


def bootstrap_admin(
    session: Session,
    *,
    email: str,
    password: str | None,
    org_name: str,
    org_slug: str,
    reset_password: bool = False,
    super_admin: bool = False,
) -> BootstrapResult:
    clean_email = email.strip().lower()
    if not _EMAIL.match(clean_email) or len(clean_email) > 320:
        raise ValueError("A valid email address is required")
    clean_slug = org_slug.strip().lower()
    if not _SLUG.match(clean_slug) or len(clean_slug) > 100:
        raise ValueError("Organization slug must use lowercase letters, digits and hyphens")
    clean_name = org_name.strip()
    if not clean_name or len(clean_name) > 200:
        raise ValueError("Organization name is required")

    organization = session.scalar(select(Organization).where(Organization.slug == clean_slug))
    organization_created = organization is None
    if organization is None:
        organization = Organization(name=clean_name, slug=clean_slug)
        session.add(organization)
        session.flush()

    user = session.scalar(select(User).where(func.lower(User.email) == clean_email))
    user_created = user is None
    password_updated = False
    if user is None or reset_password:
        _validate_password(password)
    if user is None:
        user = User(email=clean_email, password_hash=hash_password(password or ""))
        session.add(user)
        session.flush()
        password_updated = True
    elif reset_password:
        user.password_hash = hash_password(password or "")
        password_updated = True
    user.is_active = True
    if super_admin:
        user.is_super_admin = True

    membership = session.scalar(
        select(OrganizationMembership).where(
            OrganizationMembership.organization_id == organization.id,
            OrganizationMembership.user_id == user.id,
        )
    )
    membership_created = membership is None
    if membership is None:
        membership = OrganizationMembership(
            organization_id=organization.id,
            user_id=user.id,
            role=MembershipRole.ORGANIZATION_ADMIN,
        )
        session.add(membership)
    else:
        membership.role = MembershipRole.ORGANIZATION_ADMIN
    session.flush()

    session.add(
        AuditLog(
            organization_id=organization.id,
            actor_user_id=None,
            action="operator.admin_bootstrap",
            entity_type="user",
            entity_id=user.id,
            metadata_json={
                "organization_created": organization_created,
                "user_created": user_created,
                "membership_created": membership_created,
                "password_updated": password_updated,
                "super_admin": bool(super_admin),
            },
        )
    )
    session.commit()
    return BootstrapResult(
        organization_id=organization.id,
        organization_created=organization_created,
        user_id=user.id,
        user_created=user_created,
        membership_created=membership_created,
        password_updated=password_updated,
    )


def _validate_password(password: str | None) -> None:
    if not password or len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must contain at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > 1024:
        raise ValueError("Password is too long")


def _read_password(confirm: bool) -> str:
    value = os.environ.get(PASSWORD_ENV)
    if value:
        return value
    if not sys.stdin.isatty():
        raise ValueError(f"Set {PASSWORD_ENV} or run the command in an interactive terminal")
    first = getpass.getpass("Admin password: ")
    if confirm and getpass.getpass("Repeat password: ") != first:
        raise ValueError("Passwords do not match")
    return first


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Create or update a MarketingIQ org admin")
    parser.add_argument("--email", required=True)
    parser.add_argument("--org-name", default="Barmageyat")
    parser.add_argument("--org-slug", default="barmageyat")
    parser.add_argument(
        "--reset-password",
        action="store_true",
        help="Replace the password of an existing user",
    )
    parser.add_argument(
        "--super-admin",
        action="store_true",
        help="Also grant the platform super-admin flag (no implicit tenant access)",
    )
    args = parser.parse_args(argv)

    engine = create_database_engine()
    sessions = create_session_factory(engine)
    try:
        with sessions() as session:
            email = args.email.strip().lower()
            exists = session.scalar(select(User.id).where(func.lower(User.email) == email))
            needs_password = exists is None or args.reset_password
            password = _read_password(confirm=True) if needs_password else None
            result = bootstrap_admin(
                session,
                email=args.email,
                password=password,
                org_name=args.org_name,
                org_slug=args.org_slug,
                reset_password=args.reset_password,
                super_admin=args.super_admin,
            )
    except ValueError as error:
        print(json.dumps({"status": "error", "error": str(error)}))
        raise SystemExit(2) from None
    print(json.dumps({"status": "ok", **result.as_dict()}, sort_keys=True))


if __name__ == "__main__":
    main()
