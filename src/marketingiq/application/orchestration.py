from __future__ import annotations

from enum import StrEnum
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from marketingiq.application.authorization import Permission, require_permission
from marketingiq.application.contacts import ContactDiscoveryService
from marketingiq.application.decision_makers import DecisionMakerService
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

    Planning and execution deliberately stop before campaign generation, approval,
    and outbound send. Provider-credit use is always explicit.
    """

    def __init__(
        self,
        session: Session,
        tenant: TenantContext,
        provider_registry: ProviderRegistry,
        decision_maker_factory=None,
    ) -> None:
        self.session = session
        self.tenant = tenant
        self.provider_registry = provider_registry
        self.freshness = FitAssessmentFreshnessService(session, tenant)
        self._decision_maker_factory = decision_maker_factory

    def _decision_makers(self) -> DecisionMakerService:
        if self._decision_maker_factory is not None:
            return self._decision_maker_factory(self.session, self.tenant)
        return DecisionMakerService(self.session, self.tenant, self.provider_registry)

    def _persona_mode(self, product_id: str) -> bool:
        """Products with configured personas use evidence-based decision-maker discovery."""

        return DecisionMakerService(
            self.session, self.tenant, self.provider_registry, ai_client=None, page_scanner=None
        ).has_configured_personas(product_id)

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
        persona_mode = self._persona_mode(product.id)
        if persona_mode:
            # Only people who passed the evidence checks and have a verified email count.
            contact_count = (
                self._ready_count(relationship.id, qualification.id) if qualification else 0
            )
        else:
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
        decision_maker_run = None
        if persona_mode:
            decision_maker_run = (
                self._recent_decision_maker_run(relationship.id, product.id, qualification.id)
                if qualification
                else None
            )
            if not actionable:
                status, reason = "BLOCKED", "An actionable current qualification is required first"
            elif contact_count > 0:
                status, reason = "DONE", "A qualified decision-maker with a verified email exists"
            elif decision_maker_run is not None:
                status = "BLOCKED"
                reason = (
                    f"Last decision-maker search: {decision_maker_run.get('outcome')}. "
                    "Review the people found, allow provider credits, or refresh the search."
                )
            else:
                status = "READY"
                reason = "Find decision-makers for the product's personas (public sources first)"
            steps.append(
                self._step(
                    OrchestrationStep.DISCOVER_CONTACTS,
                    status,
                    reason,
                    requires_provider_credits=False,
                )
            )
            # A public-only search can be repeated once provider credits are allowed.
            steps[-1]["credit_retry_available"] = bool(
                status == "BLOCKED"
                and actionable
                and decision_maker_run is not None
                and not decision_maker_run.get("credits_allowed")
            )
        else:
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
            "contact_mode": "DECISION_MAKERS" if persona_mode else "LEGACY",
            "decision_maker_outcome": decision_maker_run.get("outcome")
            if decision_maker_run
            else None,
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
        elif step == OrchestrationStep.DISCOVER_CONTACTS and self._persona_mode(product_id):
            qualification = self._latest_qualification(relationship_id, product_id, None)
            if (
                qualification is None
                or qualification.status not in ACTIONABLE_QUALIFICATION_STATUSES
            ):
                raise ConflictError("ORCHESTRATION_ACTIONABLE_QUALIFICATION_REQUIRED")
            self._decision_makers().discover(
                relationship_id,
                product_id,
                allow_provider_credits=allow_provider_credits,
                force_refresh=False,
            )
            result_id = qualification.id
        elif step == OrchestrationStep.DISCOVER_CONTACTS:
            if not allow_provider_credits:
                raise ConflictError("ORCHESTRATION_PROVIDER_CREDIT_APPROVAL_REQUIRED")
            qualification = self._latest_qualification(relationship_id, product_id, None)
            if (
                qualification is None
                or qualification.status not in ACTIONABLE_QUALIFICATION_STATUSES
            ):
                raise ConflictError("ORCHESTRATION_ACTIONABLE_QUALIFICATION_REQUIRED")
            ContactDiscoveryService(
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

    def run_until_gate(
        self,
        relationship_id: str,
        product_id: str,
        *,
        allow_provider_credits: bool = False,
        contact_provider: str = "HUNTER",
        max_contacts: int = 10,
        max_steps: int = 4,
    ) -> dict[str, Any]:
        """Advance deterministic safe steps, stopping at an explicit gate.

        A step is executed at most once per run. This prevents loops when public
        research remains advisable but does not materially change fit inputs.
        """
        if max_steps < 1 or max_steps > 4:
            raise ValueError("max_steps must be between 1 and 4")

        executed_steps: list[dict[str, Any]] = []
        executed_names: set[str] = set()
        stop_reason = "NO_READY_STEP"

        for _ in range(max_steps):
            current = self.plan(relationship_id, product_id)
            if current["state"] == "READY_FOR_HUMAN_CAMPAIGN_REVIEW":
                stop_reason = "HUMAN_CAMPAIGN_GATE"
                break

            next_item = next(
                (
                    item
                    for item in current["steps"]
                    if (
                        item["status"] == "READY"
                        or (allow_provider_credits and item.get("credit_retry_available"))
                    )
                    and item["step"] not in executed_names
                ),
                None,
            )
            if next_item is None:
                stop_reason = "NO_READY_STEP"
                break

            step = OrchestrationStep(next_item["step"])
            if (
                step == OrchestrationStep.DISCOVER_CONTACTS
                and not allow_provider_credits
                and not current.get("contact_mode") == "DECISION_MAKERS"
            ):
                stop_reason = "PROVIDER_CREDIT_APPROVAL_REQUIRED"
                break

            result = self.execute_step(
                relationship_id,
                product_id,
                step,
                allow_provider_credits=allow_provider_credits,
                contact_provider=contact_provider,
                max_contacts=max_contacts,
            )
            executed_names.add(step.value)
            executed_steps.append({"step": step.value, "result_id": result["result_id"]})
        else:
            stop_reason = "MAX_STEPS_REACHED"

        final_plan = self.plan(relationship_id, product_id)
        if final_plan["state"] == "READY_FOR_HUMAN_CAMPAIGN_REVIEW":
            stop_reason = "HUMAN_CAMPAIGN_GATE"
        elif (
            final_plan.get("contact_mode") == "DECISION_MAKERS"
            and OrchestrationStep.DISCOVER_CONTACTS.value in executed_names
        ):
            outcome = final_plan.get("decision_maker_outcome") or "PENDING"
            stop_reason = f"DECISION_MAKERS_{outcome}"

        self.session.add(
            AuditLog(
                organization_id=self.tenant.organization_id,
                actor_user_id=self.tenant.actor_user_id,
                action="automation.run.completed",
                entity_type="organization_company",
                entity_id=relationship_id,
                metadata_json={
                    "workflow_version": WORKFLOW_VERSION,
                    "product_id": product_id,
                    "executed_steps": [item["step"] for item in executed_steps],
                    "stop_reason": stop_reason,
                    "provider_credits_explicitly_allowed": allow_provider_credits,
                },
            )
        )
        self.session.flush()
        return {
            "workflow_version": WORKFLOW_VERSION,
            "executed_steps": executed_steps,
            "stop_reason": stop_reason,
            "plan": final_plan,
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

    def _ready_count(self, relationship_id: str, qualification_id: str) -> int:
        return int(
            self.session.scalar(
                select(func.count(ContactCandidate.id)).where(
                    ContactCandidate.organization_id == self.tenant.organization_id,
                    ContactCandidate.organization_company_id == relationship_id,
                    ContactCandidate.qualification_id == qualification_id,
                    ContactCandidate.contact_status == "READY_FOR_DRAFT",
                )
            )
            or 0
        )

    def _recent_decision_maker_run(
        self, relationship_id: str, product_id: str, qualification_id: str
    ):
        """The run that should stop automatic repeats for this qualification, if any.

        A run that was allowed to spend credits is final for the qualification: automation never
        buys the same search again (a user can press Refresh). A free public-only run is repeated
        only after the search freshness window.
        """

        service = DecisionMakerService(
            self.session, self.tenant, self.provider_registry, ai_client=None, page_scanner=None
        )
        last = service.last_run(relationship_id, product_id)
        if last is None or last.get("at") is None:
            return None
        if last.get("qualification_id") != qualification_id:
            return None
        if last.get("credits_allowed"):
            return last
        if last["at"] < service.now - service.contacts.search_ttl:
            return None
        return last

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
