from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketingiq.application.authorization import Permission, require_permission
from marketingiq.application.errors import NotFoundError
from marketingiq.application.fit_freshness import FitAssessmentFreshnessService
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.models import (
    AuditLog,
    DataClassification,
    LeadQualification,
    OrganizationCompany,
    Product,
    ProductFitAssessment,
    QualificationGrade,
    QualificationStatus,
)

WORKFLOW_VERSION = "deterministic-qualification-v1"
CONFIDENCE_SCORES = {"HIGH": 100, "MEDIUM": 60, "LOW": 30}


class LeadQualificationService:
    """Turn an immutable fit assessment into an immutable, safe actionability decision."""

    def __init__(
        self, session: Session, tenant: TenantContext, now: datetime | None = None
    ) -> None:
        self.session = session
        self.tenant = tenant
        self.now = now or datetime.now(UTC)
        self.freshness = FitAssessmentFreshnessService(session, tenant, now=self.now)

    def execute(self, relationship_id: str, fit_assessment_id: str) -> LeadQualification:
        require_permission(self.tenant, Permission.RUN_QUALIFICATION)
        relationship = self._relationship(relationship_id)
        assessment = self._assessment(relationship_id, fit_assessment_id)
        product = self.session.scalar(
            select(Product).where(
                Product.id == assessment.product_id,
                Product.organization_id == self.tenant.organization_id,
            )
        )
        if product is None:
            raise NotFoundError("Product not found")

        freshness = self.freshness.evaluate(assessment)
        confidence_component = CONFIDENCE_SCORES.get(assessment.confidence, 0)
        freshness_component = 100 if freshness["is_current"] else 0
        components = {
            "fit": assessment.score,
            "coverage": assessment.evidence_coverage,
            "confidence": confidence_component,
            "freshness": freshness_component,
            "weights": {"fit": 60, "coverage": 20, "confidence": 10, "freshness": 10},
        }
        score = round(
            assessment.score * 0.60
            + assessment.evidence_coverage * 0.20
            + confidence_component * 0.10
            + freshness_component * 0.10
        )
        criteria = (assessment.explanation or {}).get("criteria", [])
        required_failed = [
            x for x in criteria if x.get("required") and x.get("result") == "NO_MATCH"
        ]
        required_unknown = [
            x for x in criteria if x.get("required") and x.get("result") == "UNKNOWN"
        ]
        gaps = self._gaps(assessment, freshness, required_unknown, product)
        status = self._status(
            assessment, freshness["is_current"], score, required_failed, required_unknown
        )
        role, role_confidence, alternatives, role_codes = self._buyer_role(product)
        positive = [
            {"code": "FIT_CRITERION_MATCH", "criterion_id": x.get("criterion_id")}
            for x in criteria
            if x.get("result") in {"MATCH", "PARTIAL_MATCH"}
        ]
        negative = [
            {
                "code": "REQUIRED_CRITERION_NO_MATCH"
                if x.get("required")
                else "FIT_CRITERION_NO_MATCH",
                "criterion_id": x.get("criterion_id"),
            }
            for x in criteria
            if x.get("result") == "NO_MATCH"
        ]
        item = LeadQualification(
            organization_id=self.tenant.organization_id,
            organization_company_id=relationship.id,
            company_id=relationship.company_id,
            product_id=product.id,
            fit_assessment_id=assessment.id,
            qualification_score=score,
            qualification_grade=self._grade(score, assessment.evidence_coverage),
            status=status,
            confidence=assessment.confidence,
            recommended_buyer_role=role,
            buyer_role_confidence=role_confidence,
            alternative_buyer_roles=alternatives,
            reasons={
                "positive_reasons": positive,
                "negative_reasons": negative,
                "buyer_role_reason_codes": role_codes,
                "lifecycle_status": relationship.lifecycle_status,
                "fit_summary": {
                    "fit_score": assessment.score,
                    "fit_grade": assessment.grade.value,
                    "evidence_coverage": assessment.evidence_coverage,
                    "fit_status": assessment.status.value,
                    "assessment_freshness": freshness["freshness_status"].value,
                },
            },
            research_gaps=gaps,
            component_scores=components,
            workflow_version=WORKFLOW_VERSION,
            qualified_at=self.now,
            created_by_user_id=self.tenant.actor_user_id,
            classification=DataClassification.MARKETINGIQ_DERIVED,
        )
        self.session.add(item)
        self.session.flush()
        self.session.add(
            AuditLog(
                organization_id=self.tenant.organization_id,
                actor_user_id=self.tenant.actor_user_id,
                action="lead_qualification.executed",
                entity_type="lead_qualification",
                entity_id=item.id,
                metadata_json={
                    "company_id": item.company_id,
                    "product_id": item.product_id,
                    "fit_assessment_id": item.fit_assessment_id,
                    "qualification_id": item.id,
                    "qualification_score": item.qualification_score,
                    "status": item.status.value,
                    "recommended_buyer_role": item.recommended_buyer_role,
                },
            )
        )
        return item

    def reevaluate(self, relationship_id: str, qualification_id: str) -> LeadQualification:
        old = self.get(relationship_id, qualification_id)
        return self.execute(relationship_id, old.fit_assessment_id)

    def list(self, relationship_id: str) -> list[LeadQualification]:
        require_permission(self.tenant, Permission.READ)
        self._relationship(relationship_id)
        return list(
            self.session.scalars(
                select(LeadQualification)
                .where(
                    LeadQualification.organization_id == self.tenant.organization_id,
                    LeadQualification.organization_company_id == relationship_id,
                )
                .order_by(LeadQualification.qualified_at.desc(), LeadQualification.id.desc())
            )
        )

    def get(self, relationship_id: str, qualification_id: str) -> LeadQualification:
        require_permission(self.tenant, Permission.READ)
        self._relationship(relationship_id)
        item = self.session.scalar(
            select(LeadQualification).where(
                LeadQualification.id == qualification_id,
                LeadQualification.organization_id == self.tenant.organization_id,
                LeadQualification.organization_company_id == relationship_id,
            )
        )
        if item is None:
            raise NotFoundError("Lead qualification not found")
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

    def _assessment(self, relationship_id: str, assessment_id: str) -> ProductFitAssessment:
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

    @staticmethod
    def _grade(score: int, coverage: int) -> QualificationGrade:
        if coverage == 0:
            return QualificationGrade.UNKNOWN
        if score >= 80:
            return QualificationGrade.A
        if score >= 65:
            return QualificationGrade.B
        if score >= 50:
            return QualificationGrade.C
        return QualificationGrade.D

    @staticmethod
    def _status(assessment, current, score, required_failed, required_unknown):
        if not current:
            return QualificationStatus.STALE
        if required_failed:
            return QualificationStatus.NOT_QUALIFIED
        if (
            required_unknown
            or assessment.evidence_coverage < 50
            or assessment.status.value in {"NEEDS_MORE_RESEARCH", "CONFLICTED", "STALE"}
        ):
            return QualificationStatus.NEEDS_MORE_RESEARCH
        if score >= 80 and assessment.score >= 80 and assessment.evidence_coverage >= 80:
            return QualificationStatus.HIGH_PRIORITY
        if score >= 60:
            return QualificationStatus.QUALIFIED
        if assessment.score < 40 and assessment.evidence_coverage >= 50:
            return QualificationStatus.NOT_QUALIFIED
        return QualificationStatus.NURTURE

    @staticmethod
    def _buyer_role(product: Product) -> tuple[str, str, list[str], list[str]]:
        primary = list(product.primary_buyer_roles or [])
        secondary = list(product.secondary_buyer_roles or [])
        if not primary:
            return "UNKNOWN", "LOW", secondary, ["INSUFFICIENT_ROLE_CONFIGURATION"]
        return primary[0], "HIGH", primary[1:] + secondary, ["PRODUCT_PRIMARY_BUYER"]

    @staticmethod
    def _gaps(assessment, freshness, required_unknown, product) -> list[dict[str, Any]]:
        gaps = [
            {
                "code": "REQUIRED_ICP_CRITERION_UNKNOWN",
                "severity": "CRITICAL",
                "criterion_id": item.get("criterion_id"),
                "fact_key": item.get("fact_key"),
                "reason": "A required ICP criterion is unknown",
            }
            for item in required_unknown
        ]
        if assessment.evidence_coverage < 50:
            gaps.append(
                {
                    "code": "LOW_EVIDENCE_COVERAGE",
                    "severity": "HIGH",
                    "criterion_id": None,
                    "fact_key": None,
                    "reason": "Evidence coverage is below 50%",
                }
            )
        if not freshness["is_current"]:
            gaps.append(
                {
                    "code": "FIT_ASSESSMENT_STALE",
                    "severity": "CRITICAL",
                    "criterion_id": None,
                    "fact_key": None,
                    "reason": "Fit assessment inputs are no longer current",
                }
            )
        if not product.primary_buyer_roles:
            gaps.append(
                {
                    "code": "BUYER_ROLE_CONFIGURATION_MISSING",
                    "severity": "MEDIUM",
                    "criterion_id": None,
                    "fact_key": None,
                    "reason": "No primary buyer role is configured",
                }
            )
        return gaps
