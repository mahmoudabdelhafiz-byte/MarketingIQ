from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from marketingiq.application.authorization import Permission, require_permission
from marketingiq.application.errors import NotFoundError
from marketingiq.application.fit_criteria import projection_quality, select_projection
from marketingiq.application.intelligence import IntelligenceService
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.models import ICP, OrganizationCompany, ProductFitAssessment


class FreshnessStatus(StrEnum):
    CURRENT = "CURRENT"
    INTELLIGENCE_CHANGED = "INTELLIGENCE_CHANGED"
    ICP_CHANGED = "ICP_CHANGED"
    INTELLIGENCE_AND_ICP_CHANGED = "INTELLIGENCE_AND_ICP_CHANGED"
    QUALITY_CHANGED = "QUALITY_CHANGED"
    NEEDS_REASSESSMENT = "NEEDS_REASSESSMENT"


class FitAssessmentFreshnessService:
    """Compare an immutable assessment snapshot with canonical projections now."""

    def __init__(
        self, session: Session, tenant: TenantContext, now: datetime | None = None
    ) -> None:
        self.session = session
        self.tenant = tenant
        self.now = now or datetime.now(UTC)
        self.intelligence = IntelligenceService(session, tenant, now=self.now)

    def get(self, relationship_id: str, assessment_id: str) -> dict[str, Any]:
        require_permission(self.tenant, Permission.READ)
        self._relationship(relationship_id)
        assessment = self.session.scalar(
            select(ProductFitAssessment).where(
                ProductFitAssessment.id == assessment_id,
                ProductFitAssessment.organization_id == self.tenant.organization_id,
                ProductFitAssessment.organization_company_id == relationship_id,
            )
        )
        if assessment is None:
            raise NotFoundError("Fit assessment not found")
        return self.evaluate(assessment)

    def evaluate(self, assessment: ProductFitAssessment) -> dict[str, Any]:
        projections = {
            item["fact_key"]: item
            for item in self.intelligence.list(assessment.organization_company_id)
        }
        old_by_id = {
            str(item.get("criterion_id")): item for item in (assessment.evidence_snapshot or [])
        }
        criteria = (assessment.explanation or {}).get("criteria", [])
        reasons: list[dict[str, Any]] = []
        intelligence_changed = False
        quality_changed = False
        changed_fact_keys: set[str] = set()
        changed_criteria: set[str] = set()

        for criterion in sorted(criteria, key=lambda item: str(item.get("criterion_id", ""))):
            criterion_id = str(criterion.get("criterion_id"))
            previous = old_by_id.get(criterion_id, criterion)
            fact_key, current = select_projection(str(criterion.get("type", "")), projections)
            previous_fact_id = previous.get("selected_fact_id")
            current_fact_id = current.get("selected_fact_id") if current else None
            previous_quality = previous.get("quality", "CURRENT")
            current_quality = projection_quality(current)
            previous_confidence = previous.get("confidence")
            current_confidence = current.get("confidence") if current else 0
            previous_value = previous.get("actual_value")
            current_value = current.get("value") if current else None
            prior_result = criterion.get("result", "UNKNOWN")
            current_result = self._compare_result(criterion, current_value)

            input_changed = (
                previous_fact_id != current_fact_id
                or previous_value != current_value
                or prior_result != current_result
            )
            detail_changed = any(
                previous.get(field) != (current.get(field) if current else default)
                for field, default in (
                    ("staleness_status", "UNKNOWN"),
                    ("conflict_status", "NONE"),
                    ("review_status", "UNREVIEWED"),
                    ("selection_reason", None),
                )
                if field in previous
            )
            confidence_changed = (
                previous_confidence is not None and previous_confidence != current_confidence
            )
            criterion_quality_changed = (
                previous_quality != current_quality or detail_changed or confidence_changed
            )
            if not input_changed and not criterion_quality_changed:
                continue

            intelligence_changed |= input_changed
            quality_changed |= criterion_quality_changed
            changed_criteria.add(criterion_id)
            effective_key = fact_key or previous.get("fact_key")
            if effective_key:
                changed_fact_keys.add(effective_key)
            code = self._reason_code(previous, current, prior_result, current_result, input_changed)
            reasons.append(
                {
                    "code": code,
                    "criterion_id": criterion_id,
                    "fact_key": effective_key,
                    "previous_fact_id": previous_fact_id,
                    "current_fact_id": current_fact_id,
                    "previous_quality": previous_quality,
                    "current_quality": current_quality,
                }
            )

        current_icp = self._current_icp(assessment)
        icp_changed = bool(current_icp and current_icp.id != assessment.icp_id)
        if icp_changed:
            reasons.append(
                {
                    "code": "ICP_SUPERSEDED",
                    "criterion_id": None,
                    "fact_key": None,
                    "previous_fact_id": None,
                    "current_fact_id": None,
                    "previous_quality": None,
                    "current_quality": None,
                }
            )
        status = self._status(intelligence_changed, icp_changed, quality_changed)
        return {
            "assessment_id": assessment.id,
            "freshness_status": status,
            "is_current": status == FreshnessStatus.CURRENT,
            "evaluated_at": assessment.evaluated_at,
            "checked_at": self.now,
            "intelligence_changed": intelligence_changed,
            "icp_changed": icp_changed,
            "quality_changed": quality_changed,
            "changed_fact_keys": sorted(changed_fact_keys),
            "changed_criteria": sorted(changed_criteria),
            "assessment_icp_id": assessment.icp_id,
            "assessment_icp_version": assessment.icp_version,
            "current_icp_id": current_icp.id if current_icp else assessment.icp_id,
            "current_icp_version": current_icp.version if current_icp else assessment.icp_version,
            "reassessment_recommended": status != FreshnessStatus.CURRENT,
            "reason": "Assessment inputs remain current"
            if status == FreshnessStatus.CURRENT
            else "A newer assessment is recommended",
            "reasons": reasons,
        }

    def _current_icp(self, assessment: ProductFitAssessment) -> ICP | None:
        original = self.session.scalar(
            select(ICP).where(
                ICP.id == assessment.icp_id,
                ICP.organization_id == self.tenant.organization_id,
            )
        )
        if original is None:
            return None
        lineage = original.logical_id or original.id
        # Name/version fallback keeps pre-lineage rows readable; explicit logical IDs win.
        lineage_filter = or_(ICP.id == lineage, ICP.logical_id == lineage)
        if original.logical_id is None:
            lineage_filter = or_(
                lineage_filter,
                (ICP.logical_id.is_(None)) & (ICP.name == original.name),
            )
        return (
            self.session.scalar(
                select(ICP)
                .where(
                    ICP.organization_id == self.tenant.organization_id,
                    ICP.product_id == assessment.product_id,
                    ICP.is_active.is_(True),
                    lineage_filter,
                )
                .order_by(ICP.version.desc(), ICP.created_at.desc(), ICP.id.desc())
            )
            or original
        )

    @staticmethod
    def _compare_result(criterion: dict[str, Any], actual: Any) -> str:
        from marketingiq.application.fit import FitAssessmentService

        return FitAssessmentService._compare(
            str(criterion.get("type", "")), criterion.get("expected_value"), actual
        )[0]

    @staticmethod
    def _reason_code(
        previous: dict[str, Any],
        current: dict[str, Any] | None,
        previous_result: str,
        current_result: str,
        input_changed: bool,
    ) -> str:
        previous_id = previous.get("selected_fact_id")
        current_id = current.get("selected_fact_id") if current else None
        previous_quality = previous.get("quality", "CURRENT")
        current_quality = projection_quality(current)
        previous_selection = previous.get("selection_reason")
        current_selection = current.get("selection_reason") if current else None
        if previous_result == "UNKNOWN" and current_result != "UNKNOWN":
            return "UNKNOWN_BECAME_KNOWN"
        if previous_selection and previous_selection.startswith("HUMAN_") and not (
            current_selection and current_selection.startswith("HUMAN_")
        ):
            return "HUMAN_OVERRIDE_REVOKED"
        if current_selection and current_selection.startswith("HUMAN_") and (
            previous_selection != current_selection or previous_id != current_id
        ):
            return "HUMAN_OVERRIDE_CHANGED"
        if previous_id != current_id:
            return "SELECTED_FACT_CHANGED"
        if previous_result != current_result:
            return "CRITERION_RESULT_CHANGED"
        previous_conflict = previous.get("conflict_status")
        current_conflict = current.get("conflict_status") if current else "NONE"
        if previous_conflict is not None and previous_conflict != current_conflict:
            return "CONFLICT_RESOLVED" if current_conflict == "NONE" else "CONFLICT_CHANGED"
        if previous_quality != current_quality:
            return f"QUALITY_{previous_quality}_TO_{current_quality}"
        if previous.get("review_status") != (current.get("review_status") if current else None):
            return "REVIEW_STATUS_CHANGED"
        if previous.get("confidence") != (current.get("confidence") if current else 0):
            return "EVIDENCE_CONFIDENCE_CHANGED"
        if previous_selection != current_selection:
            return "PROJECTION_QUALITY_CHANGED"
        return "INTELLIGENCE_INPUT_CHANGED" if input_changed else "NEEDS_REASSESSMENT"

    @staticmethod
    def _status(intelligence: bool, icp: bool, quality: bool) -> FreshnessStatus:
        if intelligence and icp:
            return FreshnessStatus.INTELLIGENCE_AND_ICP_CHANGED
        if intelligence:
            return FreshnessStatus.INTELLIGENCE_CHANGED
        if icp:
            return FreshnessStatus.ICP_CHANGED
        if quality:
            return FreshnessStatus.QUALITY_CHANGED
        return FreshnessStatus.CURRENT

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
