from dataclasses import dataclass

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from marketingiq.domain.models import OrganizationCompany, Product


@dataclass(frozen=True)
class TenantContext:
    organization_id: str
    actor_user_id: str | None = None


class TenantRepository:
    """The only application-level entry point for tenant-owned records."""

    def __init__(self, session: Session, tenant: TenantContext) -> None:
        if not tenant.organization_id:
            raise ValueError("A tenant organization is required")
        self.session = session
        self.tenant = tenant

    def products_query(self) -> Select[tuple[Product]]:
        return select(Product).where(Product.organization_id == self.tenant.organization_id)

    def organization_companies_query(self) -> Select[tuple[OrganizationCompany]]:
        return select(OrganizationCompany).where(
            OrganizationCompany.organization_id == self.tenant.organization_id
        )

    def add_product(self, product: Product) -> None:
        if product.organization_id != self.tenant.organization_id:
            raise PermissionError("Cannot write a product outside the active tenant")
        self.session.add(product)
