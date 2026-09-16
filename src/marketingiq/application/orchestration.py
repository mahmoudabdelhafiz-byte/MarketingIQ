from __future__ import annotations

from enum import StrEnum
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from marketingiq.application.authorization import Permission, require_permission
from marketingiq.application.contacts import ContactDiscoveryService
from marketingiq.application.errors import ConflictError, NotFoundError
from marketingiq.application.fit import FitAssessmentService
from marketingiq.application.fit_freshness import FitAssessmentFreshnessService
from marketingiq.application.qualification import LeadQualificationService
from marketingiq.application.research import CompanyResearchService, ProviderRegistry
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.models import (
    ICP,
    AuditLog,
    ContactCandidate,
    LeadQualification,
    OrganizationCompany,
    Product,
    ProductFitAssessment,
    QualificationStatus,
    ResearchMode,
)

WORKFLOW_VERSION = "guarded-orchestration-v1"
ACTIONABLE_QUALIFICATION_STATUSES = {
    QualificationStatus.HIGH_PRIORITY,
    QualificationStatus.QUALIFIED,
}


class OrchestrationStep(StrEnum):
    RESEARCH_PUBLIC = "RESEARCH_PUBLIC"
    ASSESS_FIT = "ASSESS_FIT"
    QUALIFY = "QUALIFY"
    DISCOVER_CONTACTS = "DISCOVER_CONTACTS"


class AutomationOrchestrationService:
    """Coordinate existing MarketingIQ services without bypassing their safeguards.

    The foundation is intentionally one-step-at-a-time. It can plan the next safe
    action and execute one explicitly requested step. It never generates, approves,
    or sends outreach and never spends provider credits without an explicit flag.
    """

    def __init__(
        self,
        session: Session,
        tenant: TenantContext,
        provider_registry: ProviderRegistry,
    ) -> None:
        self.session = session
        self.tenant = tenant
        self.provider_registry = provider_registry
        self.freshness = FitAssessmentFreshnessService(session, tenant)

    def plan(self, relationship_id: str, product_id: str) -> dict[str, Any]:
        require_permission(self.tenant, Permission.READ)
        relationship = self._relationship(relationship_id)
        product = self._product(product_id)
        active_icp = self._active_icp(product.id)
        assessment = self._latest_assessment(relationship.id, product.id)
        assessment_freshness = (
            self.freshness.get(relationship.id, assessment.id) if assessment else None
        )
        qualification = self._latest_qualification(
            relationship.id,
            product.id,
            assessment.id if assessment and assessment_freshness["is_current"] else None,
        )
        contact_count = (
            self._contact_count(relationship.id, qualification.id) if qualification else 0
        )

        steps: list[dict[str, Any]] = []
        needs_research = assessment is None or not assessment_freshness["is_current"]
        if assessment is not None and assessment.status.value in {
            "NEEDS_MORE_RESEARCH",
            "STALE",
            "CONFLICTED",
            "PARTIAL",
        }:
            needs_research = True

        steps.append(
            self._step(
                OrchestrationStep.RESEARCH_PUBLIC,
                "READY" if needs_research else "OPTIONAL",
                "Refresh public evidence before downstream decisions"
                if needs_research
                else "Current fit inputs do not require a public research refresh",
            )
        )
        steps.append(
            self._step(
                OrchestrationStep.ASSESS_FIT,
                "READY"
                if active_icp and (assessment is None or not assessment_freshness["is_current"])
                else "DONE"
                if assessment and assessment_freshness["is_current"]
                else "BLOCKED",
                "Evaluate the latest active ICP against current intelligence"
                if active_icp
                else "No active ICP is configured for this product",
            )
        )
        steps.append(
            self._step(
                OrchestrationStep.QUALIFY,
                "READY"
                if assessment and assessment_freshness["is_current"] and qualification is None
                else "DONE"
                if qualification is not None
                else "BLOCKED",
                "Create a qualification from the current fit assessment"
                if assessment and assessment_freshness["is_current"]
                else "A current fit assessment is required first",
            )
        )
        actionable = (
            qualification is not None
            and qualification.status in ACTIONABLE_QUALIFICATION_STATUSES
        )
        steps.append(
            self._step(
                OrchestrationStep.DISCOVER_CONTACTS,
                "READY"
                if actionable and contact_count == 0
                else "DONE"
                if actionable and contact_count > 0
                else "BLOCKED",
                "Find contacts aligned to the recommended buyer role"
                if actionable
                else "An actionable current qualification is required first",
                requires_provider_credits=True,
            )
        )

        next_step = next(
            (item["step"] for item in steps if item["status"] == "READY"), None
        )
        state = (
            "READY_FOR_HUMAN_CAMPAIGN_REVIEW"
            if actionable and contact_count > 0
            else "IN_PROGRESS"
        )
        return {
            "workflow_version": WORKFLOW_VERSION,
            "organization_id": self.tenant.organization_id,
            "relationship_id": relationship.id,
            "company_id": relationship.company_id,
            "product_id": product.id,
            "active_icp_id": active_icp.id if active_icp else None,
            "latest_fit_assessment_id": assessment.id if assessment else None,
            "fit_is_current": bool(assessment_freshness and assessment_freshness["is_current"]),
            "latest_qualification_id": qualification.id if qualification else None,
            "qualification_status": qualification.status.value if qualification else None,
            "contact_count": contact_count,
            "state": state,
            "next_step": next_step,
            "steps": steps,
            "human_gate": {
                "campaign_generation": True,
                "campaign_approval": True,
                "outbound_send": True,
            },
        }

    def execute_step(
        self,
        relationship_id: str,
        product_id: str,
        step: OrchestrationStep,
        *,
        allow_provider_credits: bool = False,
        contact_provider: str = "HUNTER",
        max_contacts: int = 10,
    ) -> dict[str, Any]:
        self._relationship(relationship_id)
        self._product(product_id)
        if step == OrchestrationStep.RESEARCH_PUBLIC:
            result = CompanyResearchService(
                self.session, self.tenant, self.provider_registry
            ).run(relationship_id, mode=ResearchMode.PUBLIC_ONLY, force_refresh=False)
            result_id = result.id
        elif step == OrchestrationStep.ASSESS_FIT:
            icp = self._active_icp(product_id)
            if icp is None:
                raise ConflictError("ORCHESTRATION_ACTIVE_ICP_REQUIRED")
            result = FitAssessmentService(self.session, self.tenant).evaluate(
                relationship_id, product_id, icp.id
            )
            result_id = result.id
        elif step == OrchestrationStep.QUALIFY:
            assessment = self._latest_assessment(relationship_id, product_id)
            if assessment is None:
                raise ConflictError("ORCHESTRATION_CURRENT_FIT_REQUIRED")
            freshness = self.freshness.get(relationship_id, assessment.id)
            if not freshness["is_current"]:
                raise ConflictError("ORCHESTRATION_CURRENT_FIT_REQUIRED")
            result = LeadQualificationService(self.session, self.tenant).execute(
                relationship_id, assessment.id
            )
            result_id = result.id
        elif step == OrchestrationStep.DISCOVER_CONTACTS:
            if not allow_provider_credits:
                raise ConflictError("ORCHESTRATION_PROVIDER_CREDIT_APPROVAL_REQUIRED")
            qualification = self._latest_qualification(relationship_id, product_id, None)
            if (
                qualification is None
                or qualification.status not in ACTIONABLE_QUALIFICATION_STATUSES
            ):
                raise ConflictError("ORCHESTRATION_ACTIONABLE_QUALIFICATION_REQUIRED")
            result = ContactDiscoveryService(
                self.session, self.tenant, self.provider_registry
            ).discover(
                relationship_id,
                qualification.id,
                provider_key=contact_provider,
                max_results=max_contacts,
                force_refresh=False,
            )
            result_id = qualification.id
        else:
            raise ValueError("unsupported orchestration step")

        self.session.add(
            AuditLog(
                organization_id=self.tenant.organization_id,
                actor_user_id=self.tenant.actor_user_id,
                action="automation.step.executed",
                entity_type="organization_company",
                entity_id=relationship_id,
                metadata_json={
                    "workflow_version": WORKFLOW_VERSION,
                    "product_id": product_id,
                    "step": step.value,
                    "result_id": result_id,
                    "provider_credits_explicitly_allowed": allow_provider_credits,
                },
            )
        )
        self.session.flush()
        return {
            "step": step.value,
            "result_id": result_id,
            "plan": self.plan(relationship_id, product_id),
        }

    @staticmethod
    def _step(
        step: OrchestrationStep,
        status: str,
        reason: str,
        *,
        requires_provider_credits: bool = False,
    ) -> dict[str, Any]:
        return {
            "step": step.value,
            "status": status,
            "reason": reason,
            "requires_provider_credits": requires_provider_credits,
        }

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

    def _product(self, product_id: str) -> Product:
        item = self.session.scalar(
            select(Product).where(
                Product.id == product_id,
                Product.organization_id == self.tenant.organization_id,
                Product.is_active.is_(True),
            )
        )
        if item is None:
            raise NotFoundError("Product not found")
        return item

    def _active_icp(self, product_id: str) -> ICP | None:
        return self.session.scalar(
            select(ICP)
            .where(
                ICP.organization_id == self.tenant.organization_id,
                ICP.product_id == product_id,
                ICP.is_active.is_(True),
            )
            .order_by(ICP.version.desc(), ICP.id.desc())
        )

    def _latest_assessment(
        self, relationship_id: str, product_id: str
    ) -> ProductFitAssessment | None:
        return self.session.scalar(
            select(ProductFitAssessment)
            .where(
                ProductFitAssessment.organization_id == self.tenant.organization_id,
                ProductFitAssessment.organization_company_id == relationship_id,
                ProductFitAssessment.product_id == product_id,
            )
            .order_by(ProductFitAssessment.evaluated_at.desc(), ProductFitAssessment.id.desc())
        )

    def _latest_qualification(
        self,
        relationship_id: str,
        product_id: str,
        assessment_id: str | None,
    ) -> LeadQualification | None:
        query = select(LeadQualification).where(
            LeadQualification.organization_id == self.tenant.organization_id,
            LeadQualification.organization_company_id == relationship_id,
            LeadQualification.product_id == product_id,
        )
        if assessment_id is not None:
            query = query.where(LeadQualification.fit_assessment_id == assessment_id)
        return self.session.scalar(
            query.order_by(LeadQualification.qualified_at.desc(), LeadQualification.id.desc())
        )

    def _contact_count(self, relationship_id: str, qualification_id: str) -> int:
        return int(
            self.session.scalar(
                select(func.count(ContactCandidate.id)).where(
                    ContactCandidate.organization_id == self.tenant.organization_id,
                    ContactCandidate.organization_company_id == relationship_id,
                    ContactCandidate.qualification_id == qualification_id,
                )
            )
            or 0
        )
