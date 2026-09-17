from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, FastAPI, Query
from sqlalchemy.orm import Session

from marketingiq.application.learning import ConversionIntelligenceService
from marketingiq.application.tenant import TenantContext


def register_learning_routes(
    app: FastAPI,
    prefix: str,
    tenant_dependency: Callable[..., TenantContext],
    session_dependency: Callable[..., Session],
) -> None:
    def learning_service(
        context: Annotated[TenantContext, Depends(tenant_dependency)],
        db: Annotated[Session, Depends(session_dependency)],
    ) -> ConversionIntelligenceService:
        return ConversionIntelligenceService(db, context)

    LearningService = Annotated[ConversionIntelligenceService, Depends(learning_service)]
    base = prefix + "/learning"

    @app.get(base + "/conversion-summary")
    def conversion_summary(
        svc: LearningService,
        product_id: str | None = None,
    ):
        return svc.summary(product_id)

    @app.get(base + "/buyer-role-performance")
    def buyer_role_performance(
        svc: LearningService,
        product_id: str | None = None,
        min_sample_size: int = Query(default=1, ge=1, le=1000),
    ):
        return svc.buyer_role_performance(product_id, min_sample_size)

    @app.get(base + "/message-angle-performance")
    def message_angle_performance(
        svc: LearningService,
        product_id: str | None = None,
        min_sample_size: int = Query(default=1, ge=1, le=1000),
    ):
        return svc.message_angle_performance(product_id, min_sample_size)

    @app.get(base + "/qualification-grade-performance")
    def qualification_grade_performance(
        svc: LearningService,
        product_id: str | None = None,
        min_sample_size: int = Query(default=1, ge=1, le=1000),
    ):
        return svc.qualification_grade_performance(product_id, min_sample_size)

    @app.get(base + "/industry-performance")
    def industry_performance(
        svc: LearningService,
        product_id: str | None = None,
        min_sample_size: int = Query(default=1, ge=1, le=1000),
    ):
        return svc.industry_performance(product_id, min_sample_size)

    @app.get(base + "/country-performance")
    def country_performance(
        svc: LearningService,
        product_id: str | None = None,
        min_sample_size: int = Query(default=1, ge=1, le=1000),
    ):
        return svc.country_performance(product_id, min_sample_size)

    @app.get(base + "/monthly-cohorts")
    def monthly_cohorts(
        svc: LearningService,
        product_id: str | None = None,
        min_sample_size: int = Query(default=1, ge=1, le=1000),
        limit: int = Query(default=24, ge=1, le=120),
    ):
        return svc.monthly_cohorts(product_id, min_sample_size, limit)
