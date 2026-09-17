from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketingiq.application.authorization import Permission, require_permission
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.campaigns import CampaignDraft
from marketingiq.domain.models import ContactCandidate, LeadQualification, ProductFitAssessment
from marketingiq.domain.pipeline import (
    OpportunityStage,
    SalesOpportunity,
    SalesOpportunityStageEvent,
)

FUNNEL_STAGES = (
    OpportunityStage.CONTACTED,
    OpportunityStage.RESPONDED,
    OpportunityStage.MEETING,
    OpportunityStage.PROPOSAL,
    OpportunityStage.WON,
)

SNAPSHOT_DIMENSIONS = {"industry", "country"}


class ConversionIntelligenceService:
    """Tenant-scoped, deterministic learning from observed pipeline outcomes.

    This service intentionally reports descriptive historical measurements only.
    It does not predict future conversion, rank leads, or infer causality.
    """

    def __init__(self, session: Session, tenant: TenantContext) -> None:
        self.session = session
        self.tenant = tenant

    def summary(self, product_id: str | None = None) -> dict[str, Any]:
        require_permission(self.tenant, Permission.READ)
        opportunities = self._opportunities(product_id)
        reached = self._reached(opportunities)
        counts = self._counts(opportunities, reached)
        return {
            "organization_id": self.tenant.organization_id,
            "product_id": product_id,
            "sample_size": len(opportunities),
            "counts": counts,
            "rates": self._rates(counts),
            "methodology": {
                "type": "DESCRIPTIVE_OBSERVED_OUTCOMES",
                "denominator": "tenant opportunities matching the selected scope",
                "won_requires_proposal": True,
                "lost_is_terminal": True,
                "predictive": False,
                "causal": False,
            },
        }

    def buyer_role_performance(
        self,
        product_id: str | None = None,
        min_sample_size: int = 1,
    ) -> list[dict[str, Any]]:
        return self._grouped("buyer_role", product_id, min_sample_size)

    def message_angle_performance(
        self,
        product_id: str | None = None,
        min_sample_size: int = 1,
    ) -> list[dict[str, Any]]:
        return self._grouped("message_angle", product_id, min_sample_size)

    def qualification_grade_performance(
        self,
        product_id: str | None = None,
        min_sample_size: int = 1,
    ) -> list[dict[str, Any]]:
        return self._grouped("qualification_grade", product_id, min_sample_size)

    def industry_performance(
        self,
        product_id: str | None = None,
        min_sample_size: int = 1,
    ) -> list[dict[str, Any]]:
        return self._grouped("industry", product_id, min_sample_size)

    def country_performance(
        self,
        product_id: str | None = None,
        min_sample_size: int = 1,
    ) -> list[dict[str, Any]]:
        return self._grouped("country", product_id, min_sample_size)

    def monthly_cohorts(
        self,
        product_id: str | None = None,
        min_sample_size: int = 1,
        limit: int = 24,
    ) -> list[dict[str, Any]]:
        require_permission(self.tenant, Permission.READ)
        if min_sample_size < 1 or min_sample_size > 1000:
            raise ValueError("min_sample_size must be between 1 and 1000")
        if limit < 1 or limit > 120:
            raise ValueError("limit must be between 1 and 120")

        opportunities = self._opportunities(product_id)
        reached = self._reached(opportunities)
        grouped: dict[str, list[SalesOpportunity]] = {}
        for opportunity in opportunities:
            created_at = opportunity.created_at
            cohort = f"{created_at.year:04d}-{created_at.month:02d}"
            grouped.setdefault(cohort, []).append(opportunity)

        cohorts = sorted(grouped)[-limit:]
        result: list[dict[str, Any]] = []
        for cohort in cohorts:
            items = grouped[cohort]
            if len(items) < min_sample_size:
                continue
            counts = self._counts(items, reached)
            result.append(
                {
                    "cohort": cohort,
                    "cohort_basis": "OPPORTUNITY_CREATED_AT_MONTH",
                    "outcome_basis": "OBSERVED_STAGE_HISTORY_TO_DATE",
                    "sample_size": len(items),
                    "counts": counts,
                    "rates": self._rates(counts),
                }
            )
        return result

    def _grouped(
        self,
        dimension: str,
        product_id: str | None,
        min_sample_size: int,
    ) -> list[dict[str, Any]]:
        require_permission(self.tenant, Permission.READ)
        if min_sample_size < 1 or min_sample_size > 1000:
            raise ValueError("min_sample_size must be between 1 and 1000")

        opportunities = self._opportunities(product_id)
        reached = self._reached(opportunities)
        values = self._dimension_values(opportunities, dimension)
        grouped: dict[str, list[SalesOpportunity]] = {}
        for opportunity in opportunities:
            value = values.get(opportunity.id) or "UNKNOWN"
            grouped.setdefault(value, []).append(opportunity)

        result: list[dict[str, Any]] = []
        for value, items in grouped.items():
            if len(items) < min_sample_size:
                continue
            counts = self._counts(items, reached)
            result.append(
                {
                    "dimension": dimension,
                    "dimension_source": self._dimension_source(dimension),
                    "value": value,
                    "sample_size": len(items),
                    "counts": counts,
                    "rates": self._rates(counts),
                }
            )

        return sorted(result, key=lambda item: (-item["sample_size"], item["value"]))

    def _opportunities(self, product_id: str | None) -> list[SalesOpportunity]:
        query = select(SalesOpportunity).where(
            SalesOpportunity.organization_id == self.tenant.organization_id
        )
        if product_id is not None:
            query = query.where(SalesOpportunity.product_id == product_id)
        ordered = query.order_by(SalesOpportunity.created_at, SalesOpportunity.id)
        return list(self.session.scalars(ordered))

    def _reached(
        self, opportunities: list[SalesOpportunity]
    ) -> dict[str, set[OpportunityStage]]:
        reached: dict[str, set[OpportunityStage]] = {
            item.id: {OpportunityStage.CONTACTED} for item in opportunities
        }
        if not opportunities:
            return reached
        ids = [item.id for item in opportunities]
        events = self.session.scalars(
            select(SalesOpportunityStageEvent).where(
                SalesOpportunityStageEvent.organization_id == self.tenant.organization_id,
                SalesOpportunityStageEvent.opportunity_id.in_(ids),
            )
        )
        for event in events:
            reached[event.opportunity_id].add(event.to_stage)
        return reached

    @staticmethod
    def _counts(
        opportunities: list[SalesOpportunity],
        reached: dict[str, set[OpportunityStage]],
    ) -> dict[str, int]:
        counts = {stage.value.lower(): 0 for stage in FUNNEL_STAGES}
        counts["lost"] = 0
        for opportunity in opportunities:
            stages = reached.get(opportunity.id, {OpportunityStage.CONTACTED})
            for stage in FUNNEL_STAGES:
                if stage in stages:
                    counts[stage.value.lower()] += 1
            if opportunity.stage == OpportunityStage.LOST:
                counts["lost"] += 1
        return counts

    @staticmethod
    def _rates(counts: dict[str, int]) -> dict[str, float]:
        denominator = counts["contacted"]
        if denominator == 0:
            return {
                "response_rate": 0.0,
                "meeting_rate": 0.0,
                "proposal_rate": 0.0,
                "win_rate": 0.0,
                "loss_rate": 0.0,
            }

        def pct(value: int) -> float:
            return round((value / denominator) * 100, 1)

        return {
            "response_rate": pct(counts["responded"]),
            "meeting_rate": pct(counts["meeting"]),
            "proposal_rate": pct(counts["proposal"]),
            "win_rate": pct(counts["won"]),
            "loss_rate": pct(counts["lost"]),
        }

    def _dimension_values(
        self,
        opportunities: list[SalesOpportunity],
        dimension: str,
    ) -> dict[str, str | None]:
        if not opportunities:
            return {}
        ids = [item.id for item in opportunities]
        if dimension == "buyer_role":
            rows = self.session.execute(
                select(SalesOpportunity.id, ContactCandidate.normalized_buyer_role)
                .join(ContactCandidate, ContactCandidate.id == SalesOpportunity.contact_id)
                .where(
                    SalesOpportunity.organization_id == self.tenant.organization_id,
                    SalesOpportunity.id.in_(ids),
                    ContactCandidate.organization_id == self.tenant.organization_id,
                )
            )
            return {opportunity_id: role for opportunity_id, role in rows}
        if dimension == "message_angle":
            rows = self.session.execute(
                select(SalesOpportunity.id, CampaignDraft.message_angle)
                .join(CampaignDraft, CampaignDraft.id == SalesOpportunity.draft_id)
                .where(
                    SalesOpportunity.organization_id == self.tenant.organization_id,
                    SalesOpportunity.id.in_(ids),
                    CampaignDraft.organization_id == self.tenant.organization_id,
                )
            )
            return {opportunity_id: angle for opportunity_id, angle in rows}
        if dimension == "qualification_grade":
            rows = self.session.execute(
                select(SalesOpportunity.id, LeadQualification.qualification_grade)
                .join(LeadQualification, LeadQualification.id == SalesOpportunity.qualification_id)
                .where(
                    SalesOpportunity.organization_id == self.tenant.organization_id,
                    SalesOpportunity.id.in_(ids),
                    LeadQualification.organization_id == self.tenant.organization_id,
                )
            )
            return {
                opportunity_id: grade.value if grade is not None else None
                for opportunity_id, grade in rows
            }
        if dimension in SNAPSHOT_DIMENSIONS:
            rows = self.session.execute(
                select(SalesOpportunity.id, ProductFitAssessment.evidence_snapshot)
                .join(LeadQualification, LeadQualification.id == SalesOpportunity.qualification_id)
                .join(
                    ProductFitAssessment,
                    ProductFitAssessment.id == LeadQualification.fit_assessment_id,
                )
                .where(
                    SalesOpportunity.organization_id == self.tenant.organization_id,
                    SalesOpportunity.id.in_(ids),
                    LeadQualification.organization_id == self.tenant.organization_id,
                    ProductFitAssessment.organization_id == self.tenant.organization_id,
                )
            )
            return {
                opportunity_id: self._snapshot_dimension_value(snapshot, dimension)
                for opportunity_id, snapshot in rows
            }
        raise ValueError("unsupported learning dimension")

    @staticmethod
    def _snapshot_dimension_value(snapshot: Any, fact_key: str) -> str | None:
        if not isinstance(snapshot, list):
            return None
        for item in snapshot:
            if not isinstance(item, dict) or item.get("fact_key") != fact_key:
                continue
            return ConversionIntelligenceService._safe_dimension_value(item.get("actual_value"))
        return None

    @staticmethod
    def _safe_dimension_value(value: Any) -> str | None:
        if isinstance(value, str):
            cleaned = " ".join(value.split()).strip()
            return cleaned[:200] or None
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, int | float):
            return str(value)
        if isinstance(value, list) and 0 < len(value) <= 10:
            items = [ConversionIntelligenceService._safe_dimension_value(item) for item in value]
            normalized = sorted({item for item in items if item})
            return " | ".join(normalized)[:200] if normalized else None
        return None

    @staticmethod
    def _dimension_source(dimension: str) -> str:
        if dimension == "buyer_role":
            return "CONTACT_CANDIDATE"
        if dimension == "message_angle":
            return "CAMPAIGN_DRAFT"
        if dimension == "qualification_grade":
            return "IMMUTABLE_LEAD_QUALIFICATION"
        if dimension in SNAPSHOT_DIMENSIONS:
            return "IMMUTABLE_FIT_EVIDENCE_SNAPSHOT"
        raise ValueError("unsupported learning dimension")
