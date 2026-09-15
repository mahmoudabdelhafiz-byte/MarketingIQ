from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from marketingiq.domain.models import (
    DataClassification,
    MembershipRole,
    ProductStatus,
    RedistributionStatus,
    ResearchMode,
    ReviewAction,
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
    website_url: str | None = None
    country_code: str | None = None
    industry: str | None = None
    employee_min: int | None = Field(default=None, ge=0)
    employee_max: int | None = Field(default=None, ge=0)
    description: str | None = None
    lifecycle_status: str | None = None
    private_notes: str | None = None


class CompanyRelationshipUpdate(Schema):
    lifecycle_status: str | None = Field(default=None, max_length=50)
    private_notes: str | None = Field(default=None, max_length=10000)


class CompanyIdentity(Schema):
    id: str
    canonical_name: str


class CompanyResponse(CompanyRelationshipUpdate):
    id: str
    organization_id: str
    company: CompanyIdentity


class EvidenceWrite(Schema):
    data_source_id: str | None = None
    reference_url: str | None = None
    reference_text: str | None = None
    retrieved_at: datetime | None = None
    last_verified_at: datetime | None = None


class CompanyFactWrite(Schema):
    fact_key: str = Field(min_length=1, max_length=100)
    value: Any
    classification: DataClassification
    redistribution_status: RedistributionStatus | None = None
    confidence: int = Field(ge=0, le=100)
    observed_at: datetime | None = None
    valid_until: datetime | None = None
    model_version: str | None = None
    research_run_id: str | None = None
    evidence: list[EvidenceWrite] = Field(default_factory=list)


class DataSourceWrite(Schema):
    provider_key: str
    display_name: str
    external_reference: str | None = None


class CsvImport(Schema):
    content: str


class ResearchRequest(Schema):
    mode: ResearchMode = ResearchMode.PUBLIC_ONLY
    providers: list[str] = Field(default_factory=list, max_length=10)
    force_refresh: bool = False


class IntelligenceReview(Schema):
    action: ReviewAction | Literal["REVOKE"]
    selected_fact_id: str | None = None
    value: Any = None
    confidence: int = Field(default=100, ge=0, le=100)
    note: str = Field(default="", max_length=10000)
    evidence_url: str | None = None
