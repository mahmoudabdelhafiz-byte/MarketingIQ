from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from marketingiq.domain.models import MembershipRole, ProductStatus


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
    domain: str = Field(pattern=r"^[A-Za-z0-9.-]+\.[A-Za-z]{2,}$", max_length=253)
    canonical_name: str = Field(min_length=1, max_length=255)


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
