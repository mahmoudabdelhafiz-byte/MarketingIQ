from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from marketingiq.application.authorization import Permission, require_permission
from marketingiq.application.errors import NotFoundError
from marketingiq.application.intelligence import (
    ConflictStatus,
    IntelligenceService,
    StalenessStatus,
)
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.models import (
    ICP,
    AuditLog,
    DataClassification,
    FitGrade,
    FitStatus,
    OrganizationCompany,
    Product,
    ProductFitAssessment,
)

WORKFLOW_VERSION = "deterministic-fit-v1"
SUPPORTED_FACT_KEYS = {
    "industry": ("industry",),
    "country": ("country", "country_code"),
    "employee_min": ("employee_count", "employee_range"),
    "employee_max": ("employee_count", "employee_range"),
    "company_size": ("company_size", "employee_range"),
    "active_hiring": ("active_hiring",),
    "business_service": ("business_services", "business_service"),
    "location": ("locations", "headquarters", "location"),
    "keyword": ("keywords", "company_description"),
    "presence_signal": ("presence_signals", "keywords", "company_description"),
}


class FitAssessmentService:
    def __init__(
        self, session: Session, tenant: TenantContext, now: datetime | None = None
    ) -> None:
        self.session = session
        self.tenant = tenant
        self.now = now or datetime.now(UTC)
        self.intelligence = IntelligenceService(session, tenant, now=self.now)

    def evaluate(self, relationship_id: str, product_id: str, icp_id: str) -> ProductFitAssessment:
        require_permission(self.tenant, Permission.RUN_FIT_ASSESSMENT)
        relationship = self._relationship(relationship_id)
        product = self.session.scalar(
            select(Product).where(
                Product.id == product_id, Product.organization_id == self.tenant.organization_id
            )
        )
        icp = self.session.scalar(
            select(ICP)
            .options(selectinload(ICP.criteria))
            .where(
                ICP.id == icp_id,
                ICP.product_id == product_id,
                ICP.organization_id == self.tenant.organization_id,
            )
        )
        if product is None or icp is None:
            raise NotFoundError("Product or ICP not found")

        projections = {item["fact_key"]: item for item in self.intelligence.list(relationship_id)}
        criteria = [
            {
                "id": item.id,
                "type": item.kind.casefold(),
                "expected": item.value,
                "weight": item.weight,
                "required": item.required,
            }
            for item in sorted(icp.criteria, key=lambda value: value.id)
        ]
        if icp.employee_min is not None:
            criteria.append(
                {
                    "id": "employee_min",
                    "type": "employee_min",
                    "expected": icp.employee_min,
                    "weight": 3,
                    "required": False,
                }
            )
        if icp.employee_max is not None:
            criteria.append(
                {
                    "id": "employee_max",
                    "type": "employee_max",
                    "expected": icp.employee_max,
                    "weight": 3,
                    "required": False,
                }
            )

        results = [self._evaluate_criterion(item, projections) for item in criteria]
        total = sum(item["weight"] for item in results)
        known = sum(item["weight"] for item in results if item["result"] != "UNKNOWN")
        matched = sum(
            item["weight"] * ({"MATCH": 1, "PARTIAL_MATCH": 0.5}.get(item["result"], 0))
            for item in results
        )
        failed = sum(item["weight"] for item in results if item["result"] == "NO_MATCH")
        unknown = total - known
        score = round(100 * matched / known) if known else 0
        coverage = round(100 * known / total) if total else 0
        required_failed = any(x["required"] and x["result"] == "NO_MATCH" for x in results)
        required_unknown = any(x["required"] and x["result"] == "UNKNOWN" for x in results)
        if required_failed:
            score = min(score, 39)
        grade = self._grade(score, known > 0, required_failed)
        conflicted = any(x["quality"] == "CONFLICTED" for x in results)
        stale = any(x["quality"] == "STALE" for x in results)
        if conflicted:
            status = FitStatus.CONFLICTED
        elif required_unknown:
            status = FitStatus.NEEDS_MORE_RESEARCH
        elif stale:
            status = FitStatus.STALE
        elif unknown:
            status = FitStatus.PARTIAL
        else:
            status = FitStatus.COMPLETE
        explanation = {
            "matched_weight": matched,
            "known_evaluated_weight": known,
            "unknown_weight": unknown,
            "failed_weight": failed,
            "criteria": results,
            "positive_factors": [x for x in results if x["result"] in {"MATCH", "PARTIAL_MATCH"}],
            "negative_factors": [x for x in results if x["result"] == "NO_MATCH"],
            "research_gaps": [x for x in results if x["result"] == "UNKNOWN"],
            "quality_flags": [x for x in results if x["quality"] != "CURRENT"],
        }
        assessment = ProductFitAssessment(
            organization_id=self.tenant.organization_id,
            company_id=relationship.company_id,
            organization_company_id=relationship.id,
            product_id=product.id,
            icp_id=icp.id,
            icp_version=icp.version,
            score=score,
            evidence_coverage=coverage,
            confidence="HIGH" if coverage >= 80 else "MEDIUM" if coverage >= 50 else "LOW",
            grade=grade,
            status=status,
            evaluated_at=self.now,
            workflow_version=WORKFLOW_VERSION,
            evidence_snapshot=[
                {
                    k: x[k]
                    for k in (
                        "criterion_id",
                        "selected_fact_id",
                        "actual_value",
                        "confidence",
                        "quality",
                    )
                }
                for x in results
            ],
            explanation=explanation,
            classification=DataClassification.MARKETINGIQ_DERIVED,
            created_by_user_id=self.tenant.actor_user_id,
        )
        self.session.add(assessment)
        self.session.flush()
        self.session.add(
            AuditLog(
                organization_id=self.tenant.organization_id,
                actor_user_id=self.tenant.actor_user_id,
                action="fit_assessment.executed",
                entity_type="product_fit_assessment",
                entity_id=assessment.id,
                metadata_json={
                    "company_id": relationship.company_id,
                    "product_id": product.id,
                    "icp_id": icp.id,
                    "assessment_id": assessment.id,
                    "score": score,
                    "status": status.value,
                },
            )
        )
        return assessment

    def evaluate_all(
        self, relationship_id: str, product_id: str | None = None
    ) -> list[ProductFitAssessment]:
        self._relationship(relationship_id)
        query = select(ICP).where(
            ICP.organization_id == self.tenant.organization_id, ICP.is_active.is_(True)
        )
        if product_id:
            query = query.where(ICP.product_id == product_id)
        return [
            self.evaluate(relationship_id, icp.product_id, icp.id)
            for icp in self.session.scalars(query)
        ]

    def list(self, relationship_id: str) -> list[ProductFitAssessment]:
        require_permission(self.tenant, Permission.READ)
        self._relationship(relationship_id)
        return list(
            self.session.scalars(
                select(ProductFitAssessment)
                .where(
                    ProductFitAssessment.organization_id == self.tenant.organization_id,
                    ProductFitAssessment.organization_company_id == relationship_id,
                )
                .order_by(ProductFitAssessment.evaluated_at.desc(), ProductFitAssessment.id.desc())
            )
        )

    def get(self, relationship_id: str, assessment_id: str) -> ProductFitAssessment:
        require_permission(self.tenant, Permission.READ)
        self._relationship(relationship_id)
        item = self.session.scalar(
            select(ProductFitAssessment).where(
                ProductFitAssessment.id == assessment_id,
                ProductFitAssessment.organization_id == self.tenant.organization_id,
                ProductFitAssessment.organization_company_id == relationship_id,
            )
        )
        if item is None:
            raise NotFoundError("Fit assessment not found")
        return item

    def _relationship(self, relationship_id: str) -> OrganizationCompany:
        item = self.session.scalar(
            select(OrganizationCompany).where(
                OrganizationCompany.id == relationship_id,
                OrganizationCompany.organization_id == self.tenant.organization_id,
            )
        )
        if item is None:
            raise NotFoundError("Company relationship not found")
        return item

    def _evaluate_criterion(
        self, criterion: dict[str, Any], facts: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        kind = criterion["type"]
        if kind not in SUPPORTED_FACT_KEYS:
            return {
                "criterion_id": criterion["id"],
                "type": kind,
                "expected_value": criterion["expected"],
                "actual_value": None,
                "result": "UNKNOWN",
                "confidence": 0,
                "selected_fact_id": None,
                "reason": "Criterion type is not supported by this workflow version",
                "weight": criterion["weight"],
                "required": criterion["required"],
                "quality": "CURRENT",
            }
        projection = next((facts[key] for key in SUPPORTED_FACT_KEYS[kind] if key in facts), None)
        actual = projection["value"] if projection else None
        result, reason = self._compare(kind, criterion["expected"], actual)
        quality = "CURRENT"
        if projection and projection["conflict_status"] in {
            ConflictStatus.MATERIAL,
            ConflictStatus.NEEDS_REVIEW,
        }:
            quality = "CONFLICTED"
        elif projection and projection["staleness_status"] == StalenessStatus.STALE:
            quality = "STALE"
        return {
            "criterion_id": criterion["id"],
            "type": kind,
            "expected_value": criterion["expected"],
            "actual_value": actual,
            "result": result,
            "confidence": projection["confidence"] if projection else 0,
            "selected_fact_id": projection["selected_fact_id"] if projection else None,
            "reason": reason,
            "weight": criterion["weight"],
            "required": criterion["required"],
            "quality": quality,
        }

    @staticmethod
    def _compare(kind: str, expected: Any, actual: Any) -> tuple[str, str]:
        if actual is None:
            return "UNKNOWN", "No current-best intelligence is available"
        expected_text = str(expected).strip().casefold()
        values = actual if isinstance(actual, list) else [actual]
        texts = [str(value).strip().casefold() for value in values]
        if kind in {"employee_min", "employee_max"}:
            numbers = FitAssessmentService._numbers(actual)
            if not numbers:
                return "UNKNOWN", "Current-best employee intelligence is not numeric"
            threshold = int(expected)
            matched = (
                max(numbers) >= threshold if kind == "employee_min" else min(numbers) <= threshold
            )
            return (
                ("MATCH", "Employee range satisfies threshold")
                if matched
                else ("NO_MATCH", "Employee range does not satisfy threshold")
            )
        if kind in {"keyword", "presence_signal"}:
            matched = any(expected_text in text for text in texts)
        else:
            matched = expected_text in texts
        return (
            ("MATCH", "Current-best value matches configured criterion")
            if matched
            else ("NO_MATCH", "Current-best value does not match configured criterion")
        )

    @staticmethod
    def _numbers(value: Any) -> list[int]:
        if isinstance(value, int | float):
            return [int(value)]
        if isinstance(value, dict):
            return [
                int(x) for x in (value.get("min"), value.get("max")) if isinstance(x, int | float)
            ]
        import re

        return [int(x.replace(",", "")) for x in re.findall(r"\d[\d,]*", str(value))]

    @staticmethod
    def _grade(score: int, has_evidence: bool, required_failed: bool) -> FitGrade:
        if not has_evidence:
            return FitGrade.UNKNOWN
        if required_failed:
            return FitGrade.D
        return (
            FitGrade.A
            if score >= 80
            else FitGrade.B
            if score >= 65
            else FitGrade.C
            if score >= 50
            else FitGrade.D
        )
