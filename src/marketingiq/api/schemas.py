from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from marketingiq.domain.models import (
    DataClassification,
    MembershipRole,
    ProductStatus,
    RedistributionStatus,
)


class Schema(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class LoginRequest(Schema):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=1024)


class TokenResponse(Schema):
    access_token: str
    token_type: str = "bearer"


class MembershipResponse(Schema):
    organization_id: str
    role: MembershipRole


class MeResponse(Schema):
    id: str
    email: str
    is_super_admin: bool
    memberships: list[MembershipResponse]


class Criterion(Schema):
    kind: str = Field(min_length=1, max_length=40)
    value: str = Field(min_length=1, max_length=2000)


class ProductWrite(Schema):
    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=100)
    description: str | None = Field(default=None, max_length=10000)
    value_proposition: str | None = Field(default=None, max_length=10000)
    employee_min: int | None = Field(default=None, ge=0)
    employee_max: int | None = Field(default=None, ge=0)
    criteria: list[Criterion] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_employee_range(self):
        if self.employee_min is not None and self.employee_max is not None:
            if self.employee_min > self.employee_max:
                raise ValueError("employee_min must not exceed employee_max")
        return self


class ProductResponse(ProductWrite):
    id: str
    organization_id: str
    status: ProductStatus
    created_at: datetime
    updated_at: datetime


class Activation(Schema):
    active: bool


class ICPWrite(Schema):
    name: str = Field(min_length=1, max_length=200)
    employee_min: int | None = Field(default=None, ge=0)
    employee_max: int | None = Field(default=None, ge=0)
    criteria: list[Criterion] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_employee_range(self):
        if self.employee_min is not None and self.employee_max is not None:
            if self.employee_min > self.employee_max:
                raise ValueError("employee_min must not exceed employee_max")
        return self


class ICPResponse(ICPWrite):
    id: str
    organization_id: str
    product_id: str
    version: int
    is_active: bool


class CompanyAttach(Schema):
    domain: str = Field(min_length=1, max_length=2048)
    canonical_name: str = Field(min_length=1, max_length=255)
    website_url: str | None = Field(default=None, max_length=2048)
    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    industry: str | None = Field(default=None, max_length=200)
    employee_min: int | None = Field(default=None, ge=0)
    employee_max: int | None = Field(default=None, ge=0)
    description: str | None = Field(default=None, max_length=10000)
    lifecycle_status: str | None = Field(default=None, max_length=50)
    private_notes: str | None = Field(default=None, max_length=10000)

    @model_validator(mode="after")
    def validate_employee_range(self):
        if self.employee_min is not None and self.employee_max is not None:
            if self.employee_min > self.employee_max:
                raise ValueError("employee_min must not exceed employee_max")
        return self


class CompanyRelationshipUpdate(Schema):
    lifecycle_status: str | None = Field(default=None, max_length=50)
    private_notes: str | None = Field(default=None, max_length=10000)


class CompanyIdentity(Schema):
    id: str
    canonical_name: str
    website_url: str | None = None
    country_code: str | None = None
    industry: str | None = None
    employee_min: int | None = None
    employee_max: int | None = None
    description: str | None = None


class CompanyResponse(CompanyRelationshipUpdate):
    id: str
    organization_id: str
    company_id: str
    domain: str | None = None
    company: CompanyIdentity


class EvidenceWrite(Schema):
    data_source_id: str | None = None
    reference_url: str | None = Field(default=None, max_length=2048)
    reference_text: str | None = Field(default=None, max_length=10000)
    retrieved_at: datetime | None = None
    last_verified_at: datetime | None = None


class EvidenceResponse(EvidenceWrite):
    id: str


class FactWrite(Schema):
    fact_key: str = Field(min_length=1, max_length=150)
    value: Any
    classification: DataClassification
    redistribution_status: RedistributionStatus | None = None
    confidence: int = Field(ge=0, le=100)
    observed_at: datetime | None = None
    valid_until: datetime | None = None
    model_version: str | None = Field(default=None, max_length=100)
    research_run_id: str | None = None
    evidence: list[EvidenceWrite] = Field(default_factory=list, max_length=50)


class FactResponse(Schema):
    id: str
    company_id: str
    organization_id: str | None
    fact_key: str
    value: Any
    classification: DataClassification
    redistribution_status: RedistributionStatus
    confidence: int
    observed_at: datetime
    valid_until: datetime | None
    model_version: str | None
    research_run_id: str | None
    evidence: list[EvidenceResponse]


class DataSourceWrite(Schema):
    provider_key: str = Field(min_length=1, max_length=100)
    display_name: str = Field(min_length=1, max_length=200)
    external_reference: str | None = Field(default=None, max_length=500)


class DataSourceResponse(DataSourceWrite):
    id: str
    is_active: bool


class CsvImportRequest(Schema):
    content: str = Field(min_length=1, max_length=1_000_000)


class ImportRowResponse(Schema):
    row_number: int
    company_name: str | None
    domain: str | None
    action: str
    warnings: list[str]
    errors: list[str]


class ImportReportResponse(Schema):
    total_rows: int
    valid_rows: int
    invalid_rows: int
    new_companies: int
    existing_companies: int
    new_tenant_relationships: int
    already_attached: int
    warnings: list[str]
    rows: list[ImportRowResponse]
