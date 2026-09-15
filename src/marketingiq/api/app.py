from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from marketingiq.application.companies import (
    CompanyService,
    EvidenceInput,
    FactInput,
    ImportReport,
)
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.models import DataClassification, RedistributionStatus
from marketingiq.infrastructure.database import create_database_engine, create_session_factory

app = FastAPI(title="MarketingIQ internal API", version="1.0")


@lru_cache
def _sessions() -> sessionmaker[Session]:
    return create_session_factory(create_database_engine())


def get_session():
    with _sessions() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def get_service(
    organization_id: str,
    x_user_id: Annotated[str, Header()],
    session: Annotated[Session, Depends(get_session)],
) -> CompanyService:
    return CompanyService(session, TenantContext(organization_id, x_user_id))


Service = Annotated[CompanyService, Depends(get_service)]


class CompanyCreate(BaseModel):
    domain: str
    canonical_name: str = Field(min_length=1, max_length=255)
    website_url: str | None = None
    country_code: str | None = None
    industry: str | None = None
    employee_min: int | None = Field(default=None, ge=0)
    employee_max: int | None = Field(default=None, ge=0)
    description: str | None = None
    lifecycle_status: str | None = None
    private_notes: str | None = None


class RelationshipPatch(BaseModel):
    lifecycle_status: str | None = None
    private_notes: str | None = None


class EvidenceCreate(BaseModel):
    data_source_id: str | None = None
    reference_url: str | None = None
    reference_text: str | None = None
    retrieved_at: datetime | None = None
    last_verified_at: datetime | None = None


class FactCreate(BaseModel):
    fact_key: str
    value: Any
    classification: DataClassification
    redistribution_status: RedistributionStatus | None = None
    confidence: int = Field(ge=0, le=100)
    observed_at: datetime | None = None
    valid_until: datetime | None = None
    model_version: str | None = None
    research_run_id: str | None = None
    evidence: list[EvidenceCreate] = Field(default_factory=list)


class SourceCreate(BaseModel):
    provider_key: str
    display_name: str
    external_reference: str | None = None


class CsvInput(BaseModel):
    content: str


def _call(callback):
    try:
        return callback()
    except PermissionError as error:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(error)) from error
    except LookupError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error


def _company_output(item) -> dict[str, Any]:
    domain = next(
        (
            identifier.normalized_value
            for identifier in item.company.identifiers
            if identifier.kind == "DOMAIN"
        ),
        None,
    )
    return {
        "id": item.id,
        "company_id": item.company_id,
        "domain": domain,
        "canonical_name": item.company.canonical_name,
        "website_url": item.company.website_url,
        "country_code": item.company.country_code,
        "industry": item.company.industry,
        "employee_min": item.company.employee_min,
        "employee_max": item.company.employee_max,
        "description": item.company.description,
        "lifecycle_status": item.lifecycle_status,
        "private_notes": item.private_notes,
    }


def _fact_output(item) -> dict[str, Any]:
    return {
        "id": item.id,
        "company_id": item.company_id,
        "organization_id": item.organization_id,
        "fact_key": item.fact_key,
        "value": item.value,
        "classification": item.classification,
        "redistribution_status": item.redistribution_status,
        "confidence": item.confidence,
        "observed_at": item.observed_at,
        "valid_until": item.valid_until,
        "model_version": item.model_version,
        "research_run_id": item.research_run_id,
        "evidence": [
            {
                "id": evidence.id,
                "data_source_id": evidence.data_source_id,
                "reference_url": evidence.reference_url,
                "reference_text": evidence.reference_text,
                "retrieved_at": evidence.retrieved_at,
                "last_verified_at": evidence.last_verified_at,
            }
            for evidence in item.evidences
        ],
    }


@app.get("/api/v1/organizations/{organization_id}/companies")
def list_companies(service: Service):
    return _call(lambda: [_company_output(item) for item in service.list_companies()])


@app.post("/api/v1/organizations/{organization_id}/companies", status_code=201)
def create_company(body: CompanyCreate, service: Service):
    def execute():
        shared = body.model_dump(
            exclude={"domain", "canonical_name", "lifecycle_status", "private_notes"}
        )
        item = service.attach_company(
            domain=body.domain,
            canonical_name=body.canonical_name,
            lifecycle_status=body.lifecycle_status,
            private_notes=body.private_notes,
            shared_values=shared,
        )
        return _company_output(item)

    return _call(execute)


@app.get("/api/v1/organizations/{organization_id}/companies/{relationship_id}")
def get_company(relationship_id: str, service: Service):
    return _call(lambda: _company_output(service.get_company(relationship_id)))


@app.patch("/api/v1/organizations/{organization_id}/companies/{relationship_id}")
def patch_company(relationship_id: str, body: RelationshipPatch, service: Service):
    return _call(
        lambda: _company_output(
            service.update_relationship(
                relationship_id,
                lifecycle_status=body.lifecycle_status,
                private_notes=body.private_notes,
            )
        )
    )


@app.get("/api/v1/organizations/{organization_id}/companies/{relationship_id}/facts")
def list_facts(relationship_id: str, service: Service):
    return _call(lambda: [_fact_output(item) for item in service.list_facts(relationship_id)])


@app.post(
    "/api/v1/organizations/{organization_id}/companies/{relationship_id}/facts",
    status_code=201,
)
def add_fact(relationship_id: str, body: FactCreate, service: Service):
    payload = body.model_dump()
    payload["evidence"] = [EvidenceInput(**item) for item in payload["evidence"]]
    return _call(lambda: _fact_output(service.add_fact(relationship_id, FactInput(**payload))))


@app.get("/api/v1/organizations/{organization_id}/data-sources")
def list_sources(service: Service):
    return _call(
        lambda: [
            {
                "id": source.id,
                "provider_key": source.provider_key,
                "display_name": source.display_name,
                "external_reference": source.external_reference,
                "is_active": source.is_active,
            }
            for source in service.list_sources()
        ]
    )


@app.post("/api/v1/organizations/{organization_id}/data-sources", status_code=201)
def create_source(body: SourceCreate, service: Service):
    def execute():
        source = service.get_or_create_source(
            body.provider_key, body.display_name, body.external_reference
        )
        return {
            "id": source.id,
            "provider_key": source.provider_key,
            "display_name": source.display_name,
            "external_reference": source.external_reference,
            "is_active": source.is_active,
        }

    return _call(execute)


@app.post("/api/v1/organizations/{organization_id}/company-imports/preview")
def preview_import(body: CsvInput, service: Service):
    return _call(lambda: _report_output(service.preview_csv(body.content)))


@app.post("/api/v1/organizations/{organization_id}/company-imports", status_code=201)
def execute_import(body: CsvInput, service: Service):
    return _call(lambda: _report_output(service.import_csv(body.content)))


def _report_output(report: ImportReport) -> dict[str, Any]:
    return {
        **{key: value for key, value in report.__dict__.items() if key != "rows"},
        "rows": [
            {key: value for key, value in row.__dict__.items() if key != "values"}
            for row in report.rows
        ],
    }
