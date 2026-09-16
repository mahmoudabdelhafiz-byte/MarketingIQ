from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from marketingiq.application.authorization import Permission, require_permission
from marketingiq.application.errors import ConflictError, NotFoundError
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.campaigns import CampaignDraft
from marketingiq.domain.engagement import EngagementEventType, OutreachEngagementEvent
from marketingiq.domain.models import AuditLog, OrganizationCompany
from marketingiq.domain.outbound import OutboundSendAttempt, OutboundSendStatus
from marketingiq.domain.pipeline import (
    OpportunityStage,
    SalesOpportunity,
    SalesOpportunityStageEvent,
)

STAGE_ORDER = {
    OpportunityStage.CONTACTED: 0,
    OpportunityStage.RESPONDED: 1,
    OpportunityStage.MEETING: 2,
    OpportunityStage.PROPOSAL: 3,
    OpportunityStage.WON: 4,
}
TERMINAL_STAGES = {OpportunityStage.WON, OpportunityStage.LOST}
REPLY_EVENTS = {
    EngagementEventType.REPLIED,
    EngagementEventType.POSITIVE_REPLY,
    EngagementEventType.NEGATIVE_REPLY,
}


class SalesPipelineService:
    def __init__(
        self,
        session: Session,
        tenant: TenantContext,
        now: datetime | None = None,
    ) -> None:
        self.session = session
        self.tenant = tenant
        self.now = now or datetime.now(UTC)

    def create(
        self,
        relationship_id: str,
        send_attempt_id: str,
        *,
        estimated_value: float | None = None,
        currency: str | None = None,
        next_action: str | None = None,
    ) -> SalesOpportunity:
        require_permission(self.tenant, Permission.MANAGE_PIPELINE)
        relationship = self._relationship(relationship_id)
        attempt = self._attempt(relationship, send_attempt_id)
        if attempt.status != OutboundSendStatus.SENT:
            raise ConflictError("OPPORTUNITY_REQUIRES_SENT_ATTEMPT")

        existing = self.session.scalar(
            select(SalesOpportunity).where(
                SalesOpportunity.organization_id == self.tenant.organization_id,
                SalesOpportunity.send_attempt_id == attempt.id,
            )
        )
        if existing is not None:
            return existing

        draft = self.session.scalar(
            select(CampaignDraft).where(
                CampaignDraft.id == attempt.draft_id,
                CampaignDraft.organization_id == self.tenant.organization_id,
                CampaignDraft.organization_company_id == relationship.id,
            )
        )
        if draft is None:
            raise NotFoundError("Campaign draft not found")

        initial_stage = self._initial_stage(attempt.id)
        clean_currency = self._currency(currency, estimated_value)
        clean_next_action = self._optional_text(next_action, 500, "next_action")
        if estimated_value is not None and estimated_value < 0:
            raise ValueError("estimated_value must be zero or greater")

        opportunity = SalesOpportunity(
            organization_id=self.tenant.organization_id,
            organization_company_id=relationship.id,
            company_id=relationship.company_id,
            product_id=draft.product_id,
            qualification_id=draft.qualification_id,
            contact_id=draft.contact_id,
            draft_id=draft.id,
            send_attempt_id=attempt.id,
            stage=initial_stage,
            estimated_value=estimated_value,
            currency=clean_currency,
            next_action=clean_next_action,
            owner_user_id=self.tenant.actor_user_id,
            created_by_user_id=self.tenant.actor_user_id,
            created_at=self.now,
            updated_at=self.now,
        )
        self.session.add(opportunity)
        self.session.flush()
        self._append_stage_event(
            opportunity,
            None,
            initial_stage,
            note=None,
            reason_code="CREATED_FROM_SENT_OUTREACH",
            occurred_at=self.now,
        )
        self._audit("sales_opportunity.created", opportunity, initial_stage)
        return opportunity

    def list(self, relationship_id: str) -> list[SalesOpportunity]:
        require_permission(self.tenant, Permission.READ)
        relationship = self._relationship(relationship_id)
        return list(
            self.session.scalars(
                select(SalesOpportunity)
                .where(
                    SalesOpportunity.organization_id == self.tenant.organization_id,
                    SalesOpportunity.organization_company_id == relationship.id,
                )
                .order_by(SalesOpportunity.updated_at.desc(), SalesOpportunity.id.desc())
            )
        )

    def get(self, relationship_id: str, opportunity_id: str) -> SalesOpportunity:
        require_permission(self.tenant, Permission.READ)
        relationship = self._relationship(relationship_id)
        opportunity = self.session.scalar(
            select(SalesOpportunity).where(
                SalesOpportunity.id == opportunity_id,
                SalesOpportunity.organization_id == self.tenant.organization_id,
                SalesOpportunity.organization_company_id == relationship.id,
            )
        )
        if opportunity is None:
            raise NotFoundError("Sales opportunity not found")
        return opportunity

    def history(
        self, relationship_id: str, opportunity_id: str
    ) -> list[SalesOpportunityStageEvent]:
        opportunity = self.get(relationship_id, opportunity_id)
        return list(
            self.session.scalars(
                select(SalesOpportunityStageEvent)
                .where(
                    SalesOpportunityStageEvent.organization_id
                    == self.tenant.organization_id,
                    SalesOpportunityStageEvent.opportunity_id == opportunity.id,
                )
                .order_by(SalesOpportunityStageEvent.sequence_number)
            )
        )

    def move_stage(
        self,
        relationship_id: str,
        opportunity_id: str,
        stage: OpportunityStage,
        *,
        note: str | None = None,
        reason_code: str | None = None,
        occurred_at: datetime | None = None,
    ) -> SalesOpportunity:
        require_permission(self.tenant, Permission.MANAGE_PIPELINE)
        opportunity = self.get(relationship_id, opportunity_id)
        current = opportunity.stage
        if current in TERMINAL_STAGES:
            raise ConflictError("OPPORTUNITY_STAGE_IS_TERMINAL")
        if stage == current:
            return opportunity
        self._validate_transition(current, stage)

        clean_note = self._optional_text(note, 2000, "note")
        clean_reason = self._optional_text(reason_code, 100, "reason_code")
        when = occurred_at or self.now
        opportunity.stage = stage
        opportunity.updated_at = self.now
        self._append_stage_event(
            opportunity,
            current,
            stage,
            note=clean_note,
            reason_code=clean_reason,
            occurred_at=when,
        )
        self._audit("sales_opportunity.stage_changed", opportunity, stage)
        return opportunity

    def update_details(
        self,
        relationship_id: str,
        opportunity_id: str,
        *,
        estimated_value: float | None,
        currency: str | None,
        next_action: str | None,
    ) -> SalesOpportunity:
        require_permission(self.tenant, Permission.MANAGE_PIPELINE)
        opportunity = self.get(relationship_id, opportunity_id)
        if estimated_value is not None and estimated_value < 0:
            raise ValueError("estimated_value must be zero or greater")
        opportunity.estimated_value = estimated_value
        opportunity.currency = self._currency(currency, estimated_value)
        opportunity.next_action = self._optional_text(next_action, 500, "next_action")
        opportunity.updated_at = self.now
        self._audit("sales_opportunity.updated", opportunity, opportunity.stage)
        return opportunity

    def _initial_stage(self, attempt_id: str) -> OpportunityStage:
        has_reply = self.session.scalar(
            select(OutreachEngagementEvent.id)
            .where(
                OutreachEngagementEvent.organization_id == self.tenant.organization_id,
                OutreachEngagementEvent.send_attempt_id == attempt_id,
                OutreachEngagementEvent.event_type.in_(REPLY_EVENTS),
            )
            .limit(1)
        )
        if has_reply is not None:
            return OpportunityStage.RESPONDED
        return OpportunityStage.CONTACTED

    def _append_stage_event(
        self,
        opportunity: SalesOpportunity,
        from_stage: OpportunityStage | None,
        to_stage: OpportunityStage,
        *,
        note: str | None,
        reason_code: str | None,
        occurred_at: datetime,
    ) -> None:
        sequence = self.session.scalar(
            select(func.count(SalesOpportunityStageEvent.id)).where(
                SalesOpportunityStageEvent.opportunity_id == opportunity.id
            )
        )
        self.session.add(
            SalesOpportunityStageEvent(
                organization_id=self.tenant.organization_id,
                opportunity_id=opportunity.id,
                sequence_number=int(sequence or 0) + 1,
                from_stage=from_stage,
                to_stage=to_stage,
                note=note,
                reason_code=reason_code,
                occurred_at=occurred_at,
                created_by_user_id=self.tenant.actor_user_id,
                created_at=self.now,
            )
        )
        self.session.flush()

    def _relationship(self, relationship_id: str) -> OrganizationCompany:
        relationship = self.session.scalar(
            select(OrganizationCompany).where(
                OrganizationCompany.id == relationship_id,
                OrganizationCompany.organization_id == self.tenant.organization_id,
            )
        )
        if relationship is None:
            raise NotFoundError("Company relationship not found")
        return relationship

    def _attempt(
        self, relationship: OrganizationCompany, attempt_id: str
    ) -> OutboundSendAttempt:
        attempt = self.session.scalar(
            select(OutboundSendAttempt).where(
                OutboundSendAttempt.id == attempt_id,
                OutboundSendAttempt.organization_id == self.tenant.organization_id,
                OutboundSendAttempt.organization_company_id == relationship.id,
                OutboundSendAttempt.company_id == relationship.company_id,
            )
        )
        if attempt is None:
            raise NotFoundError("Outbound send attempt not found")
        return attempt

    @staticmethod
    def _validate_transition(current: OpportunityStage, target: OpportunityStage) -> None:
        if target == OpportunityStage.LOST:
            return
        if target == OpportunityStage.WON and current != OpportunityStage.PROPOSAL:
            raise ConflictError("OPPORTUNITY_WON_REQUIRES_PROPOSAL")
        if target not in STAGE_ORDER or current not in STAGE_ORDER:
            raise ConflictError("INVALID_OPPORTUNITY_STAGE_TRANSITION")
        if STAGE_ORDER[target] <= STAGE_ORDER[current]:
            raise ConflictError("OPPORTUNITY_STAGE_CANNOT_MOVE_BACKWARD")

    @staticmethod
    def _currency(value: str | None, estimated_value: float | None) -> str | None:
        if value is None:
            if estimated_value is not None:
                raise ValueError("currency is required when estimated_value is provided")
            return None
        clean = value.strip().upper()
        if len(clean) != 3 or not clean.isalpha():
            raise ValueError("currency must be a 3-letter code")
        return clean

    @staticmethod
    def _optional_text(value: str | None, limit: int, field: str) -> str | None:
        if value is None:
            return None
        clean = value.strip()
        if not clean:
            return None
        if len(clean) > limit:
            raise ValueError(f"{field} must not exceed {limit} characters")
        return clean

    def _audit(
        self,
        action: str,
        opportunity: SalesOpportunity,
        stage: OpportunityStage,
    ) -> None:
        self.session.add(
            AuditLog(
                organization_id=self.tenant.organization_id,
                actor_user_id=self.tenant.actor_user_id,
                action=action,
                entity_type="sales_opportunity",
                entity_id=opportunity.id,
                metadata_json={
                    "company_id": opportunity.company_id,
                    "product_id": opportunity.product_id,
                    "qualification_id": opportunity.qualification_id,
                    "contact_id": opportunity.contact_id,
                    "send_attempt_id": opportunity.send_attempt_id,
                    "stage": stage.value,
                },
            )
        )
