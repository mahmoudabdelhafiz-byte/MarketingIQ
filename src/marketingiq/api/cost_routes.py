"""Cost dashboard: estimated spend per lead, reply and meeting, and the unit prices behind it."""

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, FastAPI, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from marketingiq.application.costs import CostReportService
from marketingiq.application.dashboard import DashboardService
from marketingiq.application.tenant import TenantContext


class CostSettingsRequest(BaseModel):
    currency: str = Field(default="USD", min_length=3, max_length=3)
    provider_credit_cost: float | None = Field(default=None, ge=0, le=1_000_000)
    ai_input_per_million: float | None = Field(default=None, ge=0, le=1_000_000)
    ai_output_per_million: float | None = Field(default=None, ge=0, le=1_000_000)
    web_search_cost: float | None = Field(default=None, ge=0, le=1_000_000)
    monthly_fixed_cost: float | None = Field(default=None, ge=0, le=1_000_000)


def register_cost_routes(
    app: FastAPI,
    prefix: str,
    tenant_dependency: Callable[..., TenantContext],
    session_dependency: Callable[..., Session],
) -> None:
    def cost_service(
        context: Annotated[TenantContext, Depends(tenant_dependency)],
        db: Annotated[Session, Depends(session_dependency)],
    ) -> CostReportService:
        return CostReportService(db, context)

    Costs = Annotated[CostReportService, Depends(cost_service)]

    @app.get(prefix + "/costs/report")
    def cost_report(svc: Costs, days: Annotated[int, Query(ge=1, le=366)] = 30):
        return svc.report(days)

    @app.get(prefix + "/dashboard")
    def dashboard(
        context: Annotated[TenantContext, Depends(tenant_dependency)],
        db: Annotated[Session, Depends(session_dependency)],
        days: Annotated[int, Query(ge=1, le=366)] = 90,
    ):
        return DashboardService(db, context).build(days)

    @app.get(prefix + "/costs/settings")
    def cost_settings(svc: Costs):
        return svc.describe_settings()

    @app.put(prefix + "/costs/settings")
    def update_cost_settings(body: CostSettingsRequest, svc: Costs):
        return svc.update_settings(**body.model_dump())
