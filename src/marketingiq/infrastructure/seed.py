"""Idempotent, non-production sample catalog. Run explicitly in a development database."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketingiq.domain.models import Organization, Product, ProductStatus

PRODUCTS = ("CardIQ", "AttendanceIQ", "ManpowerIQ", "TechSelectAI")


def seed_development_data(session: Session) -> Organization:
    organization = session.scalar(select(Organization).where(Organization.slug == "barmageyat"))
    if organization is None:
        organization = Organization(name="Barmageyat", slug="barmageyat")
        session.add(organization)
        session.flush()
    existing = set(
        session.scalars(
            select(Product.slug).where(Product.organization_id == organization.id)
        ).all()
    )
    for name in PRODUCTS:
        slug = name.lower()
        if slug not in existing:
            session.add(
                Product(
                    organization_id=organization.id,
                    name=name,
                    slug=slug,
                    status=ProductStatus.DRAFT,
                )
            )
    session.commit()
    return organization
