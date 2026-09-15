import os
from collections.abc import Generator
from typing import Any

import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from marketingiq.api.schemas import (
    Activation,
    CompanyAttach,
    CompanyFactWrite,
    CompanyRelationshipUpdate,
    CsvImport,
    DataSourceWrite,
    ICPResponse,
    ICPWrite,
    LoginRequest,
    MeResponse,
    ProductResponse,
    ProductWrite,
    TokenResponse,
)
from marketingiq.application.auth import authenticate, create_access_token, decode_access_token
from marketingiq.application.catalog import CatalogService
from marketingiq.application.companies import CompanyService, EvidenceInput, FactInput, ImportReport
from marketingiq.application.errors import AuthorizationError, ConflictError, NotFoundError
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.models import OrganizationMembership, User
from marketingiq.infrastructure.database import create_database_engine, create_session_factory

bearer = HTTPBearer(auto_error=False)


def create_app(database_url: str | None = None, auth_secret: str | None = None) -> FastAPI:
    secret = auth_secret or os.environ.get("AUTH_SECRET")
    if not secret or len(secret) < 32:
        raise RuntimeError("AUTH_SECRET must contain at least 32 characters")
    sessions = create_session_factory(create_database_engine(database_url))
    app = FastAPI(title="MarketingIQ internal API", version="1.0.0")

    def session() -> Generator[Session, None, None]:
        with sessions() as value:
            try:
                yield value
                value.commit()
            except Exception:
                value.rollback()
                raise

    def current_user(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
        db: Session = Depends(session),
    ) -> User:
        if credentials is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
        try:
            user_id = decode_access_token(credentials.credentials, secret)
        except jwt.PyJWTError:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token") from None
        user = db.get(User, user_id)
        if user is None or not user.is_active:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
        return user

    def tenant(
        org_id: str,
        user: User = Depends(current_user),
        db: Session = Depends(session),
        x_organization_id: str | None = Header(default=None),
    ) -> TenantContext:
        if x_organization_id is not None and x_organization_id != org_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Organization context mismatch")
        membership = db.scalar(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == org_id,
                OrganizationMembership.user_id == user.id,
            )
        )
        if membership is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
        return TenantContext(org_id, user.id, membership.role)

    def service(
        context: TenantContext = Depends(tenant), db: Session = Depends(session)
    ) -> CatalogService:
        return CatalogService(db, context)

    def company_service(
        context: TenantContext = Depends(tenant), db: Session = Depends(session)
    ) -> CompanyService:
        return CompanyService(db, context)

    @app.exception_handler(NotFoundError)
    async def not_found(_request, error):
        return JSONResponse({"detail": str(error)}, status_code=404)

    @app.exception_handler(AuthorizationError)
    async def forbidden(_request, error):
        return JSONResponse({"detail": str(error)}, status_code=403)

    @app.exception_handler(ConflictError)
    async def conflict(_request, error):
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.exception_handler(LookupError)
    async def company_not_found(_request, error):
        return JSONResponse({"detail": str(error)}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid_company_input(_request, error):
        return JSONResponse({"detail": str(error)}, status_code=422)

    @app.post("/api/v1/auth/login", response_model=TokenResponse)
    def login(body: LoginRequest, db: Session = Depends(session)):
        user = authenticate(db, body.email, body.password)
        if user is None:
            raise HTTPException(401, "Invalid email or password")
        return TokenResponse(access_token=create_access_token(user, secret))

    @app.get("/api/v1/me", response_model=MeResponse)
    def me(user: User = Depends(current_user), db: Session = Depends(session)):
        memberships = db.scalars(
            select(OrganizationMembership).where(OrganizationMembership.user_id == user.id)
        ).all()
        return MeResponse(
            id=user.id,
            email=user.email,
            is_super_admin=user.is_super_admin,
            memberships=memberships,
        )

    prefix = "/api/v1/organizations/{org_id}"

    @app.get(prefix + "/products", response_model=list[ProductResponse])
    def products(svc: CatalogService = Depends(service)):
        return svc.list_products()

    @app.post(prefix + "/products", response_model=ProductResponse, status_code=201)
    def create_product(body: ProductWrite, svc: CatalogService = Depends(service)):
        return svc.create_product(**body.model_dump())

    @app.get(prefix + "/products/{product_id}", response_model=ProductResponse)
    def product(product_id: str, svc: CatalogService = Depends(service)):
        return svc.get_product(product_id)

    @app.put(prefix + "/products/{product_id}", response_model=ProductResponse)
    def update_product(product_id: str, body: ProductWrite, svc: CatalogService = Depends(service)):
        return svc.update_product(product_id, **body.model_dump())

    @app.patch(prefix + "/products/{product_id}/activation", response_model=ProductResponse)
    def activate_product(product_id: str, body: Activation, svc: CatalogService = Depends(service)):
        return svc.set_product_active(product_id, body.active)

    @app.get(prefix + "/products/{product_id}/icps", response_model=list[ICPResponse])
    def icps(product_id: str, svc: CatalogService = Depends(service)):
        return svc.list_icps(product_id)

    @app.post(prefix + "/products/{product_id}/icps", response_model=ICPResponse, status_code=201)
    def create_icp(product_id: str, body: ICPWrite, svc: CatalogService = Depends(service)):
        return svc.create_icp(product_id, **body.model_dump())

    @app.get(prefix + "/icps/{icp_id}", response_model=ICPResponse)
    def icp(icp_id: str, svc: CatalogService = Depends(service)):
        return svc.get_icp(icp_id)

    @app.put(prefix + "/icps/{icp_id}", response_model=ICPResponse)
    def update_icp(icp_id: str, body: ICPWrite, svc: CatalogService = Depends(service)):
        return svc.update_icp(icp_id, **body.model_dump())

    @app.patch(prefix + "/icps/{icp_id}/activation", response_model=ICPResponse)
    def activate_icp(icp_id: str, body: Activation, svc: CatalogService = Depends(service)):
        return svc.set_icp_active(icp_id, body.active)

    @app.get(prefix + "/companies")
    def companies(svc: CompanyService = Depends(company_service)):
        return [_company_output(item) for item in svc.list_companies()]

    @app.post(prefix + "/companies", status_code=201)
    def attach_company(body: CompanyAttach, svc: CompanyService = Depends(company_service)):
        values = body.model_dump()
        shared = {
            key: values.pop(key)
            for key in (
                "website_url", "country_code", "industry", "employee_min", "employee_max",
                "description",
            )
        }
        return _company_output(svc.attach_company(**values, shared_values=shared))

    @app.get(prefix + "/companies/{relationship_id}")
    def company(relationship_id: str, svc: CompanyService = Depends(company_service)):
        return _company_output(svc.get_company(relationship_id))

    @app.patch(prefix + "/companies/{relationship_id}")
    def update_company(
        relationship_id: str,
        body: CompanyRelationshipUpdate,
        svc: CompanyService = Depends(company_service),
    ):
        return _company_output(svc.update_relationship(relationship_id, **body.model_dump()))

    @app.get(prefix + "/companies/{relationship_id}/facts")
    def facts(relationship_id: str, svc: CompanyService = Depends(company_service)):
        return [_fact_output(item) for item in svc.list_facts(relationship_id)]

    @app.post(prefix + "/companies/{relationship_id}/facts", status_code=201)
    def add_fact(
        relationship_id: str,
        body: CompanyFactWrite,
        svc: CompanyService = Depends(company_service),
    ):
        payload = body.model_dump()
        payload["evidence"] = [EvidenceInput(**item) for item in payload["evidence"]]
        return _fact_output(svc.add_fact(relationship_id, FactInput(**payload)))

    @app.get(prefix + "/data-sources")
    def sources(svc: CompanyService = Depends(company_service)):
        return [_source_output(item) for item in svc.list_sources()]

    @app.post(prefix + "/data-sources", status_code=201)
    def create_source(body: DataSourceWrite, svc: CompanyService = Depends(company_service)):
        return _source_output(svc.get_or_create_source(**body.model_dump()))

    @app.post(prefix + "/company-imports/preview")
    def preview_import(body: CsvImport, svc: CompanyService = Depends(company_service)):
        return _report_output(svc.preview_csv(body.content))

    @app.post(prefix + "/company-imports", status_code=201)
    def execute_import(body: CsvImport, svc: CompanyService = Depends(company_service)):
        return _report_output(svc.import_csv(body.content))

    return app


def _company_output(item) -> dict[str, Any]:
    domain = next(
        (value.normalized_value for value in item.company.identifiers if value.kind == "DOMAIN"),
        None,
    )
    return {
        "id": item.id, "organization_id": item.organization_id,
        "company_id": item.company_id, "company": {
            "id": item.company.id, "canonical_name": item.company.canonical_name,
        }, "domain": domain,
        "canonical_name": item.company.canonical_name, "website_url": item.company.website_url,
        "country_code": item.company.country_code, "industry": item.company.industry,
        "employee_min": item.company.employee_min, "employee_max": item.company.employee_max,
        "description": item.company.description, "lifecycle_status": item.lifecycle_status,
        "private_notes": item.private_notes,
    }


def _fact_output(item) -> dict[str, Any]:
    return {
        "id": item.id, "company_id": item.company_id,
        "organization_id": item.organization_id, "fact_key": item.fact_key,
        "value": item.value, "classification": item.classification,
        "redistribution_status": item.redistribution_status, "confidence": item.confidence,
        "observed_at": item.observed_at, "valid_until": item.valid_until,
        "model_version": item.model_version, "research_run_id": item.research_run_id,
        "evidence": [
            {
                "id": evidence.id, "data_source_id": evidence.data_source_id,
                "reference_url": evidence.reference_url, "reference_text": evidence.reference_text,
                "retrieved_at": evidence.retrieved_at,
                "last_verified_at": evidence.last_verified_at,
            }
            for evidence in item.evidences
        ],
    }


def _source_output(source) -> dict[str, Any]:
    return {
        "id": source.id, "provider_key": source.provider_key,
        "display_name": source.display_name, "external_reference": source.external_reference,
        "is_active": source.is_active,
    }


def _report_output(report: ImportReport) -> dict[str, Any]:
    return {
        **{key: value for key, value in report.__dict__.items() if key != "rows"},
        "rows": [
            {key: value for key, value in row.__dict__.items() if key != "values"}
            for row in report.rows
        ],
    }
