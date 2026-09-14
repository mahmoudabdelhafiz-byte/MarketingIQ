from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from marketingiq.application.authorization import Permission, require_permission
from marketingiq.application.errors import ConflictError, NotFoundError
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.models import (
    ICP,
    AuditLog,
    Company,
    CompanyIdentifier,
    ICPCriterion,
    OrganizationCompany,
    Product,
    ProductCriterion,
    ProductStatus,
)


@dataclass(frozen=True)
class CriterionData:
    kind: str
    value: str


def _audit(session: Session, tenant: TenantContext, action: str, entity: object) -> None:
    session.add(
        AuditLog(
            organization_id=tenant.organization_id,
            actor_user_id=tenant.actor_user_id,
            action=action,
            entity_type=type(entity).__name__,
            entity_id=entity.id,
        )
    )


class CatalogService:
    def __init__(self, session: Session, tenant: TenantContext) -> None:
        self.session, self.tenant = session, tenant

    def _commit(self) -> None:
        try:
            self.session.commit()
        except IntegrityError as error:
            self.session.rollback()
            raise ConflictError("The requested resource conflicts with existing data") from error

    def list_products(self) -> list[Product]:
        return list(
            self.session.scalars(
                select(Product)
                .options(selectinload(Product.criteria))
                .where(Product.organization_id == self.tenant.organization_id)
            ).all()
        )

    def get_product(self, product_id: str) -> Product:
        product = self.session.scalar(
            select(Product)
            .options(selectinload(Product.criteria))
            .where(Product.id == product_id, Product.organization_id == self.tenant.organization_id)
        )
        if product is None:
            raise NotFoundError("Product not found")
        return product

    def create_product(self, **data: object) -> Product:
        require_permission(self.tenant, Permission.WRITE_CATALOG)
        criteria = data.pop("criteria", [])
        product = Product(organization_id=self.tenant.organization_id, **data)
        product.criteria = [ProductCriterion(kind=item.kind, value=item.value) for item in criteria]
        self.session.add(product)
        self.session.flush()
        _audit(self.session, self.tenant, "product.created", product)
        self._commit()
        return product

    def update_product(self, product_id: str, **data: object) -> Product:
        require_permission(self.tenant, Permission.WRITE_CATALOG)
        product = self.get_product(product_id)
        criteria = data.pop("criteria", None)
        for key, value in data.items():
            setattr(product, key, value)
        if criteria is not None:
            product.criteria = [
                ProductCriterion(kind=item.kind, value=item.value) for item in criteria
            ]
        _audit(self.session, self.tenant, "product.updated", product)
        self._commit()
        return product

    def set_product_active(self, product_id: str, active: bool) -> Product:
        return self.update_product(
            product_id, status=ProductStatus.ACTIVE if active else ProductStatus.ARCHIVED
        )

    def list_icps(self, product_id: str) -> list[ICP]:
        self.get_product(product_id)
        return list(
            self.session.scalars(
                select(ICP)
                .options(selectinload(ICP.criteria))
                .where(
                    ICP.organization_id == self.tenant.organization_id, ICP.product_id == product_id
                )
            ).all()
        )

    def get_icp(self, icp_id: str) -> ICP:
        icp = self.session.scalar(
            select(ICP)
            .options(selectinload(ICP.criteria))
            .where(ICP.id == icp_id, ICP.organization_id == self.tenant.organization_id)
        )
        if icp is None:
            raise NotFoundError("ICP not found")
        return icp

    def create_icp(self, product_id: str, **data: object) -> ICP:
        require_permission(self.tenant, Permission.WRITE_CATALOG)
        self.get_product(product_id)
        criteria = data.pop("criteria", [])
        icp = ICP(organization_id=self.tenant.organization_id, product_id=product_id, **data)
        icp.criteria = [ICPCriterion(kind=x.kind, value=x.value) for x in criteria]
        self.session.add(icp)
        self.session.flush()
        _audit(self.session, self.tenant, "icp.created", icp)
        self._commit()
        return icp

    def update_icp(self, icp_id: str, **data: object) -> ICP:
        """Create a new revision, leaving the prior ICP immutable and inactive."""
        require_permission(self.tenant, Permission.WRITE_CATALOG)
        old = self.get_icp(icp_id)
        old.is_active = False
        criteria = data.pop("criteria", [CriterionData(x.kind, x.value) for x in old.criteria])
        values = {k: data.pop(k, getattr(old, k)) for k in ("name", "employee_min", "employee_max")}
        revision = ICP(
            organization_id=self.tenant.organization_id,
            product_id=old.product_id,
            version=old.version + 1,
            is_active=data.pop("is_active", True),
            **values,
        )
        revision.criteria = [ICPCriterion(kind=x.kind, value=x.value) for x in criteria]
        self.session.add(revision)
        self.session.flush()
        _audit(self.session, self.tenant, "icp.revised", revision)
        self._commit()
        return revision

    def set_icp_active(self, icp_id: str, active: bool) -> ICP:
        require_permission(self.tenant, Permission.WRITE_CATALOG)
        icp = self.get_icp(icp_id)
        icp.is_active = active
        _audit(self.session, self.tenant, "icp.activation_changed", icp)
        self._commit()
        return icp

    def list_companies(self) -> list[OrganizationCompany]:
        return list(
            self.session.scalars(
                select(OrganizationCompany)
                .options(
                    selectinload(OrganizationCompany.company).selectinload(Company.identifiers)
                )
                .where(OrganizationCompany.organization_id == self.tenant.organization_id)
            ).all()
        )

    def get_company(self, relationship_id: str) -> OrganizationCompany:
        item = self.session.scalar(
            select(OrganizationCompany)
            .options(selectinload(OrganizationCompany.company).selectinload(Company.identifiers))
            .where(
                OrganizationCompany.id == relationship_id,
                OrganizationCompany.organization_id == self.tenant.organization_id,
            )
        )
        if item is None:
            raise NotFoundError("Company relationship not found")
        return item

    def attach_company(self, domain: str, canonical_name: str) -> OrganizationCompany:
        require_permission(self.tenant, Permission.WRITE_CATALOG)
        normalized = domain.strip().lower()
        identifier = self.session.scalar(
            select(CompanyIdentifier).where(
                CompanyIdentifier.kind == "DOMAIN", CompanyIdentifier.normalized_value == normalized
            )
        )
        company = identifier and self.session.get(Company, identifier.company_id)
        if company is None:
            company = Company(canonical_name=canonical_name)
            company.identifiers.append(
                CompanyIdentifier(
                    kind="DOMAIN", value=domain, normalized_value=normalized, is_primary=True
                )
            )
        relation = OrganizationCompany(organization_id=self.tenant.organization_id, company=company)
        self.session.add(relation)
        self.session.flush()
        _audit(self.session, self.tenant, "company.attached", relation)
        self._commit()
        return self.get_company(relation.id)

    def update_company_relationship(
        self, relationship_id: str, **data: object
    ) -> OrganizationCompany:
        require_permission(self.tenant, Permission.WRITE_CATALOG)
        item = self.get_company(relationship_id)
        for key, value in data.items():
            setattr(item, key, value)
        _audit(self.session, self.tenant, "company.relationship_updated", item)
        self._commit()
        return item
