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
    CampaignDraftStatus,
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
    """Create evidence-grounded outreach copy without sending or mutating prior drafts."""

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
        if not cta:
            raise ValueError("call_to_action must not be blank")
        if len(cta) > 500:
            raise ValueError("call_to_action must not exceed 500 characters")

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
        self.session.add(
            AuditLog(
                organization_id=self.tenant.organization_id,
                actor_user_id=self.tenant.actor_user_id,
                action="campaign_draft.generated",
                entity_type="campaign_draft",
                entity_id=draft.id,
                metadata_json={
                    "company_id": relationship.company_id,
                    "product_id": product.id,
                    "qualification_id": qualification.id,
                    "contact_id": contact.id,
                    "channel": channel.value,
                    "message_angle": message_angle,
                    "workflow_version": WORKFLOW_VERSION,
                },
            )
        )
        return draft

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
