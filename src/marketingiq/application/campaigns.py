from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketingiq.application.authorization import Permission, require_permission
from marketingiq.application.errors import ConflictError, NotFoundError
from marketingiq.application.fit_freshness import FitAssessmentFreshnessService
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.campaigns import (
    CampaignChannel,
    CampaignDraft,
    CampaignDraftReviewEvent,
    CampaignDraftStatus,
    CampaignReviewAction,
)
from marketingiq.domain.models import (
    AuditLog,
    BuyerRoleMatch,
    Company,
    ContactCandidate,
    ContactEmail,
    DataClassification,
    EmailVerificationStatus,
    LeadQualification,
    OrganizationCompany,
    Product,
    QualificationStatus,
    RedistributionStatus,
)

WORKFLOW_VERSION = "deterministic-campaign-draft-v1"
DEFAULT_CTA = (
    "Would a 20-minute conversation be useful to explore whether this could fit your priorities?"
)
PURSUIT_STATUSES = {QualificationStatus.HIGH_PRIORITY, QualificationStatus.QUALIFIED}
ALIGNED_ROLE_MATCHES = {
    BuyerRoleMatch.EXACT,
    BuyerRoleMatch.STRONG,
    BuyerRoleMatch.PARTIAL,
}


class CampaignDraftService:
    """Create grounded drafts and preserve all human review actions as immutable events."""

    def __init__(
        self, session: Session, tenant: TenantContext, now: datetime | None = None
    ) -> None:
        self.session = session
        self.tenant = tenant
        self.now = now or datetime.now(UTC)
        self.freshness = FitAssessmentFreshnessService(session, tenant, now=self.now)

    def generate(
        self,
        relationship_id: str,
        qualification_id: str,
        contact_id: str,
        channel: CampaignChannel = CampaignChannel.EMAIL,
        call_to_action: str | None = None,
    ) -> CampaignDraft:
        require_permission(self.tenant, Permission.GENERATE_CAMPAIGN_DRAFT)
        if channel != CampaignChannel.EMAIL:
            raise ConflictError("CAMPAIGN_DRAFT_CHANNEL_UNSUPPORTED")

        relationship = self._relationship(relationship_id)
        qualification = self._qualification(relationship, qualification_id)
        contact = self._contact(relationship, qualification, contact_id)
        product = self._product(qualification.product_id)

        if not (product.value_proposition or "").strip():
            raise ConflictError("CAMPAIGN_DRAFT_VALUE_PROPOSITION_REQUIRED")
        if contact.buyer_role_match not in ALIGNED_ROLE_MATCHES:
            raise ConflictError("CAMPAIGN_DRAFT_BUYER_ROLE_ALIGNMENT_REQUIRED")
        if not self._has_verified_email(contact.id):
            raise ConflictError("CAMPAIGN_DRAFT_VERIFIED_EMAIL_REQUIRED")

        cta = (call_to_action or DEFAULT_CTA).strip()
        self._validate_content("placeholder", "placeholder", cta, allow_placeholders=True)

        company = self.session.get(Company, relationship.company_id)
        if company is None:
            raise NotFoundError("Company not found")

        subject, body = self._compose(company.canonical_name, product, contact, cta)
        positive_criteria = sorted(
            {
                str(item.get("criterion_id"))
                for item in (qualification.reasons or {}).get("positive_reasons", [])
                if item.get("criterion_id")
            }
        )
        fit_summary = (qualification.reasons or {}).get("fit_summary", {})
        message_angle = (
            "HIGH_FIT_ROLE_ALIGNMENT"
            if qualification.status == QualificationStatus.HIGH_PRIORITY
            else "QUALIFIED_ROLE_ALIGNMENT"
        )

        draft = CampaignDraft(
            organization_id=self.tenant.organization_id,
            organization_company_id=relationship.id,
            company_id=relationship.company_id,
            product_id=product.id,
            qualification_id=qualification.id,
            contact_id=contact.id,
            channel=channel,
            status=CampaignDraftStatus.DRAFT,
            subject=subject,
            body=body,
            call_to_action=cta,
            message_angle=message_angle,
            personalization_snapshot={
                "company_id": relationship.company_id,
                "product_id": product.id,
                "qualification_id": qualification.id,
                "contact_id": contact.id,
                "recommended_buyer_role": qualification.recommended_buyer_role,
                "buyer_role_match": contact.buyer_role_match.value,
                "qualification_status": qualification.status.value,
            },
            evidence_snapshot={
                "fit_assessment_id": qualification.fit_assessment_id,
                "qualification_id": qualification.id,
                "qualification_score": qualification.qualification_score,
                "fit_score": fit_summary.get("fit_score"),
                "fit_grade": fit_summary.get("fit_grade"),
                "criterion_ids": positive_criteria,
            },
            workflow_version=WORKFLOW_VERSION,
            created_by_user_id=self.tenant.actor_user_id,
            created_at=self.now,
            classification=DataClassification.MARKETINGIQ_DERIVED,
            redistribution_status=RedistributionStatus.INTERNAL_ONLY,
        )
        self.session.add(draft)
        self.session.flush()
        self._audit(
            "campaign_draft.generated",
            draft,
            {
                "channel": channel.value,
                "message_angle": message_angle,
                "workflow_version": WORKFLOW_VERSION,
            },
        )
        return draft

    def edit(
        self,
        relationship_id: str,
        draft_id: str,
        *,
        subject: str | None = None,
        body: str | None = None,
        call_to_action: str | None = None,
        reason: str | None = None,
    ) -> CampaignDraftReviewEvent:
        require_permission(self.tenant, Permission.REVIEW_CAMPAIGN_DRAFT)
        draft = self.get(relationship_id, draft_id)
        current = self.effective_state_for(draft)
        if current["status"] == CampaignDraftStatus.APPROVED:
            raise ConflictError("CAMPAIGN_DRAFT_ALREADY_APPROVED")
        if subject is None and body is None and call_to_action is None:
            raise ValueError("At least one editable field is required")

        next_subject = current["subject"] if subject is None else subject.strip()
        next_body = current["body"] if body is None else body.strip()
        next_cta = current["call_to_action"] if call_to_action is None else call_to_action.strip()
        self._validate_content(next_subject, next_body, next_cta)
        event = self._append_event(
            draft,
            CampaignReviewAction.EDIT,
            CampaignDraftStatus.DRAFT,
            next_subject,
            next_body,
            next_cta,
            reason,
        )
        self._audit("campaign_draft.edited", draft, {"revision_number": event.revision_number})
        return event

    def approve(
        self, relationship_id: str, draft_id: str, reason: str | None = None
    ) -> CampaignDraftReviewEvent:
        require_permission(self.tenant, Permission.REVIEW_CAMPAIGN_DRAFT)
        draft = self.get(relationship_id, draft_id)
        current = self.effective_state_for(draft)
        if current["status"] == CampaignDraftStatus.APPROVED:
            raise ConflictError("CAMPAIGN_DRAFT_ALREADY_APPROVED")
        if current["status"] == CampaignDraftStatus.REJECTED:
            raise ConflictError("CAMPAIGN_DRAFT_EDIT_REQUIRED_AFTER_REJECTION")

        self._validate_for_approval(draft, relationship_id)
        event = self._append_event(
            draft,
            CampaignReviewAction.APPROVE,
            CampaignDraftStatus.APPROVED,
            current["subject"],
            current["body"],
            current["call_to_action"],
            reason,
        )
        self._audit("campaign_draft.approved", draft, {"revision_number": event.revision_number})
        return event

    def reject(
        self, relationship_id: str, draft_id: str, reason: str
    ) -> CampaignDraftReviewEvent:
        require_permission(self.tenant, Permission.REVIEW_CAMPAIGN_DRAFT)
        draft = self.get(relationship_id, draft_id)
        current = self.effective_state_for(draft)
        if current["status"] == CampaignDraftStatus.APPROVED:
            raise ConflictError("CAMPAIGN_DRAFT_ALREADY_APPROVED")
        if current["status"] == CampaignDraftStatus.REJECTED:
            raise ConflictError("CAMPAIGN_DRAFT_ALREADY_REJECTED")
        clean_reason = reason.strip()
        if not clean_reason:
            raise ValueError("Rejection reason is required")
        if len(clean_reason) > 500:
            raise ValueError("reason must not exceed 500 characters")
        event = self._append_event(
            draft,
            CampaignReviewAction.REJECT,
            CampaignDraftStatus.REJECTED,
            current["subject"],
            current["body"],
            current["call_to_action"],
            clean_reason,
        )
        self._audit("campaign_draft.rejected", draft, {"revision_number": event.revision_number})
        return event

    def history(self, relationship_id: str, draft_id: str) -> list[CampaignDraftReviewEvent]:
        require_permission(self.tenant, Permission.READ)
        draft = self.get(relationship_id, draft_id)
        return list(
            self.session.scalars(
                select(CampaignDraftReviewEvent)
                .where(
                    CampaignDraftReviewEvent.organization_id == self.tenant.organization_id,
                    CampaignDraftReviewEvent.draft_id == draft.id,
                )
                .order_by(CampaignDraftReviewEvent.revision_number.asc())
            )
        )

    def effective_state(self, relationship_id: str, draft_id: str) -> dict:
        require_permission(self.tenant, Permission.READ)
        return self.effective_state_for(self.get(relationship_id, draft_id))

    def effective_state_for(self, draft: CampaignDraft) -> dict:
        latest = self._latest_event(draft.id)
        if latest is None:
            return {
                "status": CampaignDraftStatus.DRAFT,
                "subject": draft.subject,
                "body": draft.body,
                "call_to_action": draft.call_to_action,
                "revision_number": 0,
                "last_action": None,
                "last_action_at": None,
            }
        return {
            "status": latest.status,
            "subject": latest.subject,
            "body": latest.body,
            "call_to_action": latest.call_to_action,
            "revision_number": latest.revision_number,
            "last_action": latest.action,
            "last_action_at": latest.created_at,
        }

    def list(self, relationship_id: str) -> list[CampaignDraft]:
        require_permission(self.tenant, Permission.READ)
        self._relationship(relationship_id)
        return list(
            self.session.scalars(
                select(CampaignDraft)
                .where(
                    CampaignDraft.organization_id == self.tenant.organization_id,
                    CampaignDraft.organization_company_id == relationship_id,
                )
                .order_by(CampaignDraft.created_at.desc(), CampaignDraft.id.desc())
            )
        )

    def get(self, relationship_id: str, draft_id: str) -> CampaignDraft:
        require_permission(self.tenant, Permission.READ)
        self._relationship(relationship_id)
        draft = self.session.scalar(
            select(CampaignDraft).where(
                CampaignDraft.id == draft_id,
                CampaignDraft.organization_id == self.tenant.organization_id,
                CampaignDraft.organization_company_id == relationship_id,
            )
        )
        if draft is None:
            raise NotFoundError("Campaign draft not found")
        return draft

    def _validate_for_approval(self, draft: CampaignDraft, relationship_id: str) -> None:
        relationship = self._relationship(relationship_id)
        qualification = self._qualification(relationship, draft.qualification_id)
        contact = self._contact(relationship, qualification, draft.contact_id)
        if contact.buyer_role_match not in ALIGNED_ROLE_MATCHES:
            raise ConflictError("CAMPAIGN_DRAFT_BUYER_ROLE_ALIGNMENT_REQUIRED")
        if not self._has_verified_email(contact.id):
            raise ConflictError("CAMPAIGN_DRAFT_VERIFIED_EMAIL_REQUIRED")

    def _append_event(
        self,
        draft: CampaignDraft,
        action: CampaignReviewAction,
        status: CampaignDraftStatus,
        subject: str,
        body: str,
        call_to_action: str,
        reason: str | None,
    ) -> CampaignDraftReviewEvent:
        clean_reason = reason.strip() if reason else None
        if clean_reason and len(clean_reason) > 500:
            raise ValueError("reason must not exceed 500 characters")
        latest = self._latest_event(draft.id)
        revision_number = 1 if latest is None else latest.revision_number + 1
        event = CampaignDraftReviewEvent(
            organization_id=self.tenant.organization_id,
            draft_id=draft.id,
            revision_number=revision_number,
            action=action,
            status=status,
            subject=subject,
            body=body,
            call_to_action=call_to_action,
            reason=clean_reason,
            created_by_user_id=self.tenant.actor_user_id,
            created_at=self.now,
        )
        self.session.add(event)
        self.session.flush()
        return event

    def _latest_event(self, draft_id: str) -> CampaignDraftReviewEvent | None:
        return self.session.scalar(
            select(CampaignDraftReviewEvent)
            .where(
                CampaignDraftReviewEvent.organization_id == self.tenant.organization_id,
                CampaignDraftReviewEvent.draft_id == draft_id,
            )
            .order_by(CampaignDraftReviewEvent.revision_number.desc())
            .limit(1)
        )

    def _audit(self, action: str, draft: CampaignDraft, extra: dict | None = None) -> None:
        metadata = {
            "company_id": draft.company_id,
            "product_id": draft.product_id,
            "qualification_id": draft.qualification_id,
            "contact_id": draft.contact_id,
            "channel": draft.channel.value,
        }
        if extra:
            metadata.update(extra)
        self.session.add(
            AuditLog(
                organization_id=self.tenant.organization_id,
                actor_user_id=self.tenant.actor_user_id,
                action=action,
                entity_type="campaign_draft",
                entity_id=draft.id,
                metadata_json=metadata,
            )
        )

    @staticmethod
    def _validate_content(
        subject: str,
        body: str,
        call_to_action: str,
        *,
        allow_placeholders: bool = False,
    ) -> None:
        if not allow_placeholders:
            if not subject.strip():
                raise ValueError("subject must not be blank")
            if len(subject) > 255:
                raise ValueError("subject must not exceed 255 characters")
            if not body.strip():
                raise ValueError("body must not be blank")
            if len(body) > 20000:
                raise ValueError("body must not exceed 20000 characters")
        if not call_to_action.strip():
            raise ValueError("call_to_action must not be blank")
        if len(call_to_action) > 500:
            raise ValueError("call_to_action must not exceed 500 characters")

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

    def _qualification(
        self, relationship: OrganizationCompany, qualification_id: str
    ) -> LeadQualification:
        item = self.session.scalar(
            select(LeadQualification).where(
                LeadQualification.id == qualification_id,
                LeadQualification.organization_id == self.tenant.organization_id,
                LeadQualification.organization_company_id == relationship.id,
                LeadQualification.company_id == relationship.company_id,
            )
        )
        if item is None:
            raise NotFoundError("Lead qualification not found")
        if item.status not in PURSUIT_STATUSES:
            raise ConflictError("CAMPAIGN_DRAFT_QUALIFICATION_NOT_ACTIONABLE")
        freshness = self.freshness.get(relationship.id, item.fit_assessment_id)
        if not freshness["is_current"]:
            raise ConflictError("CAMPAIGN_DRAFT_REQUALIFICATION_REQUIRED")
        return item

    def _contact(
        self,
        relationship: OrganizationCompany,
        qualification: LeadQualification,
        contact_id: str,
    ) -> ContactCandidate:
        item = self.session.scalar(
            select(ContactCandidate).where(
                ContactCandidate.id == contact_id,
                ContactCandidate.organization_id == self.tenant.organization_id,
                ContactCandidate.organization_company_id == relationship.id,
                ContactCandidate.company_id == relationship.company_id,
                ContactCandidate.qualification_id == qualification.id,
            )
        )
        if item is None:
            raise NotFoundError("Contact not found for this qualification")
        return item

    def _product(self, product_id: str) -> Product:
        product = self.session.scalar(
            select(Product).where(
                Product.id == product_id,
                Product.organization_id == self.tenant.organization_id,
            )
        )
        if product is None:
            raise NotFoundError("Product not found")
        return product

    def _has_verified_email(self, contact_id: str) -> bool:
        return (
            self.session.scalar(
                select(ContactEmail.id).where(
                    ContactEmail.contact_id == contact_id,
                    ContactEmail.verification_status == EmailVerificationStatus.VALID,
                )
            )
            is not None
        )

    @staticmethod
    def _compose(
        company_name: str,
        product: Product,
        contact: ContactCandidate,
        call_to_action: str,
    ) -> tuple[str, str]:
        greeting = f"Hi {contact.first_name.strip()}," if contact.first_name else "Hello,"
        if contact.job_title:
            relevance = (
                f"Given your role as {contact.job_title.strip()} at {company_name}, "
                f"I thought {product.name} may be relevant."
            )
        else:
            relevance = (
                f"Given your work at {company_name}, "
                f"I thought {product.name} may be relevant."
            )
        subject = f"{product.name} for {company_name}"
        body = "\n\n".join(
            [
                greeting,
                relevance,
                product.value_proposition.strip(),
                call_to_action,
                "Best regards,",
            ]
        )
        return subject, body
