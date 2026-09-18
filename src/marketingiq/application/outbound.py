from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parseaddr

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketingiq.application.authorization import Permission, require_permission
from marketingiq.application.errors import ConflictError, NotFoundError
from marketingiq.application.fit_freshness import FitAssessmentFreshnessService
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.campaigns import (
    CampaignDraft,
    CampaignDraftReviewEvent,
    CampaignDraftStatus,
    CampaignReviewAction,
)
from marketingiq.domain.models import (
    AuditLog,
    BuyerRoleMatch,
    ContactCandidate,
    ContactEmail,
    EmailVerificationStatus,
    LeadQualification,
    OrganizationCompany,
    QualificationStatus,
)
from marketingiq.domain.outbound import (
    EmailSender,
    OutboundMessage,
    OutboundProviderError,
    OutboundProviderRejected,
    OutboundSendAttempt,
    OutboundSendStatus,
    SuppressionEntry,
    SuppressionSource,
)

PURSUIT_STATUSES = {QualificationStatus.HIGH_PRIORITY, QualificationStatus.QUALIFIED}
ALIGNED_ROLE_MATCHES = {
    BuyerRoleMatch.EXACT,
    BuyerRoleMatch.STRONG,
    BuyerRoleMatch.PARTIAL,
}


class OutboundSenderRegistry:
    def __init__(self, senders: list[EmailSender]) -> None:
        self._senders = {sender.key.upper(): sender for sender in senders}

    def get(self, key: str) -> EmailSender:
        try:
            return self._senders[key.upper()]
        except KeyError as error:
            raise ValueError(f"Unknown outbound provider: {key}") from error

    def status(self) -> list[dict]:
        return [
            {
                "provider_key": key,
                "configured": sender.configured,
                "status": "available" if sender.configured else "not_configured",
            }
            for key, sender in sorted(self._senders.items())
        ]


class OutboundSendService:
    """Perform explicit, approved-draft-only B2B email sends with safety gates."""

    def __init__(
        self,
        session: Session,
        tenant: TenantContext,
        registry: OutboundSenderRegistry,
        now: datetime | None = None,
    ) -> None:
        self.session = session
        self.tenant = tenant
        self.registry = registry
        self.now = now or datetime.now(UTC)
        self.freshness = FitAssessmentFreshnessService(session, tenant, now=self.now)

    def send(
        self,
        relationship_id: str,
        draft_id: str,
        *,
        provider_key: str,
        idempotency_key: str,
    ) -> OutboundSendAttempt:
        require_permission(self.tenant, Permission.SEND_OUTBOUND_EMAIL)
        relationship = self._relationship(relationship_id)
        draft = self._draft(relationship, draft_id)
        clean_key = idempotency_key.strip()
        if len(clean_key) < 8 or len(clean_key) > 100:
            raise ValueError("idempotency_key must contain 8 to 100 characters")

        existing = self.session.scalar(
            select(OutboundSendAttempt).where(
                OutboundSendAttempt.organization_id == self.tenant.organization_id,
                OutboundSendAttempt.idempotency_key == clean_key,
            )
        )
        if existing is not None:
            if existing.draft_id != draft.id:
                raise ConflictError("OUTBOUND_IDEMPOTENCY_KEY_REUSED")
            return existing

        approval = self._approved_revision(draft)
        qualification = self._qualification(relationship, draft)
        contact = self._contact(relationship, draft, qualification)
        email = self._verified_email(contact.id)
        self._ensure_not_suppressed(email.email)

        previously_sent = self.session.scalar(
            select(OutboundSendAttempt.id).where(
                OutboundSendAttempt.organization_id == self.tenant.organization_id,
                OutboundSendAttempt.draft_id == draft.id,
                OutboundSendAttempt.status == OutboundSendStatus.SENT,
            )
        )
        if previously_sent is not None:
            raise ConflictError("OUTBOUND_DRAFT_ALREADY_SENT")

        sender = self.registry.get(provider_key)
        attempt = OutboundSendAttempt(
            organization_id=self.tenant.organization_id,
            organization_company_id=relationship.id,
            company_id=relationship.company_id,
            draft_id=draft.id,
            review_event_id=approval.id,
            contact_id=contact.id,
            contact_email_id=email.id,
            provider_key=sender.key,
            idempotency_key=clean_key,
            status=OutboundSendStatus.PENDING,
            requested_by_user_id=self.tenant.actor_user_id,
            requested_at=self.now,
        )
        self.session.add(attempt)
        self.session.flush()

        if not sender.configured:
            return self._fail(attempt, "NOT_CONFIGURED")

        try:
            result = sender.send(
                OutboundMessage(
                    recipient=email.email,
                    subject=approval.subject,
                    body=approval.body,
                    idempotency_key=clean_key,
                )
            )
            if not result.accepted:
                raise OutboundProviderRejected("Outbound provider rejected the message")
        except OutboundProviderError as error:
            return self._fail(attempt, error.category)

        attempt.status = OutboundSendStatus.SENT
        attempt.provider_message_id = result.provider_message_id
        attempt.completed_at = self.now
        self._audit(
            "outbound_send.sent",
            attempt,
            {"provider": sender.key, "status": attempt.status.value},
        )
        return attempt

    def list_attempts(self, relationship_id: str, draft_id: str) -> list[OutboundSendAttempt]:
        require_permission(self.tenant, Permission.READ)
        relationship = self._relationship(relationship_id)
        draft = self._draft(relationship, draft_id)
        return list(
            self.session.scalars(
                select(OutboundSendAttempt)
                .where(
                    OutboundSendAttempt.organization_id == self.tenant.organization_id,
                    OutboundSendAttempt.organization_company_id == relationship.id,
                    OutboundSendAttempt.draft_id == draft.id,
                )
                .order_by(OutboundSendAttempt.requested_at.desc(), OutboundSendAttempt.id.desc())
            )
        )

    def suppress(
        self,
        email: str,
        *,
        reason: str | None = None,
        source: SuppressionSource = SuppressionSource.MANUAL,
    ) -> SuppressionEntry:
        require_permission(self.tenant, Permission.MANAGE_OUTBOUND_SUPPRESSIONS)
        normalized = self._normalize_email(email)
        existing = self.session.scalar(
            select(SuppressionEntry).where(
                SuppressionEntry.organization_id == self.tenant.organization_id,
                SuppressionEntry.email_normalized == normalized,
            )
        )
        if existing is not None:
            return existing
        clean_reason = reason.strip() if reason else None
        if clean_reason and len(clean_reason) > 500:
            raise ValueError("reason must not exceed 500 characters")
        entry = SuppressionEntry(
            organization_id=self.tenant.organization_id,
            email_normalized=normalized,
            source=source,
            reason=clean_reason,
            created_by_user_id=self.tenant.actor_user_id,
            created_at=self.now,
        )
        self.session.add(entry)
        self.session.flush()
        self.session.add(
            AuditLog(
                organization_id=self.tenant.organization_id,
                actor_user_id=self.tenant.actor_user_id,
                action="outbound_suppression.created",
                entity_type="outbound_suppression",
                entity_id=entry.id,
                metadata_json={"source": source.value},
            )
        )
        return entry

    def list_suppressions(self) -> list[SuppressionEntry]:
        require_permission(self.tenant, Permission.READ)
        return list(
            self.session.scalars(
                select(SuppressionEntry)
                .where(SuppressionEntry.organization_id == self.tenant.organization_id)
                .order_by(SuppressionEntry.created_at.desc(), SuppressionEntry.id.desc())
            )
        )

    def provider_status(self) -> list[dict]:
        require_permission(self.tenant, Permission.READ)
        return self.registry.status()

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

    def _draft(self, relationship: OrganizationCompany, draft_id: str) -> CampaignDraft:
        draft = self.session.scalar(
            select(CampaignDraft).where(
                CampaignDraft.id == draft_id,
                CampaignDraft.organization_id == self.tenant.organization_id,
                CampaignDraft.organization_company_id == relationship.id,
                CampaignDraft.company_id == relationship.company_id,
            )
        )
        if draft is None:
            raise NotFoundError("Campaign draft not found")
        return draft

    def _approved_revision(self, draft: CampaignDraft) -> CampaignDraftReviewEvent:
        event = self.session.scalar(
            select(CampaignDraftReviewEvent)
            .where(
                CampaignDraftReviewEvent.organization_id == self.tenant.organization_id,
                CampaignDraftReviewEvent.draft_id == draft.id,
            )
            .order_by(CampaignDraftReviewEvent.revision_number.desc())
            .limit(1)
        )
        if (
            event is None
            or event.status != CampaignDraftStatus.APPROVED
            or event.action != CampaignReviewAction.APPROVE
        ):
            raise ConflictError("OUTBOUND_DRAFT_NOT_APPROVED")
        return event

    def _qualification(
        self, relationship: OrganizationCompany, draft: CampaignDraft
    ) -> LeadQualification:
        qualification = self.session.scalar(
            select(LeadQualification).where(
                LeadQualification.id == draft.qualification_id,
                LeadQualification.organization_id == self.tenant.organization_id,
                LeadQualification.organization_company_id == relationship.id,
                LeadQualification.company_id == relationship.company_id,
            )
        )
        if qualification is None:
            raise NotFoundError("Lead qualification not found")
        if qualification.status not in PURSUIT_STATUSES:
            raise ConflictError("OUTBOUND_QUALIFICATION_NOT_ACTIONABLE")
        current = self.freshness.get(relationship.id, qualification.fit_assessment_id)
        if not current["is_current"]:
            raise ConflictError("OUTBOUND_REQUALIFICATION_REQUIRED")
        return qualification

    def _contact(
        self,
        relationship: OrganizationCompany,
        draft: CampaignDraft,
        qualification: LeadQualification,
    ) -> ContactCandidate:
        contact = self.session.scalar(
            select(ContactCandidate).where(
                ContactCandidate.id == draft.contact_id,
                ContactCandidate.organization_id == self.tenant.organization_id,
                ContactCandidate.organization_company_id == relationship.id,
                ContactCandidate.company_id == relationship.company_id,
                ContactCandidate.qualification_id == qualification.id,
            )
        )
        if contact is None:
            raise NotFoundError("Contact not found for this qualification")
        if contact.buyer_role_match not in ALIGNED_ROLE_MATCHES:
            raise ConflictError("OUTBOUND_BUYER_ROLE_ALIGNMENT_REQUIRED")
        return contact

    def _verified_email(self, contact_id: str) -> ContactEmail:
        email = self.session.scalar(
            select(ContactEmail)
            .where(
                ContactEmail.contact_id == contact_id,
                ContactEmail.verification_status == EmailVerificationStatus.VALID,
            )
            .order_by(ContactEmail.verified_at.desc(), ContactEmail.id.desc())
            .limit(1)
        )
        if email is None:
            raise ConflictError("OUTBOUND_VERIFIED_EMAIL_REQUIRED")
        return email

    def _ensure_not_suppressed(self, email: str) -> None:
        normalized = self._normalize_email(email)
        blocked = self.session.scalar(
            select(SuppressionEntry.id).where(
                SuppressionEntry.organization_id == self.tenant.organization_id,
                SuppressionEntry.email_normalized == normalized,
            )
        )
        if blocked is not None:
            raise ConflictError("OUTBOUND_RECIPIENT_SUPPRESSED")

    def _fail(self, attempt: OutboundSendAttempt, category: str) -> OutboundSendAttempt:
        attempt.status = OutboundSendStatus.FAILED
        attempt.error_category = category[:50]
        attempt.completed_at = self.now
        self._audit(
            "outbound_send.failed",
            attempt,
            {"provider": attempt.provider_key, "error_category": attempt.error_category},
        )
        return attempt

    def _audit(self, action: str, attempt: OutboundSendAttempt, extra: dict) -> None:
        metadata = {
            "company_id": attempt.company_id,
            "draft_id": attempt.draft_id,
            "contact_id": attempt.contact_id,
            "send_attempt_id": attempt.id,
            **extra,
        }
        self.session.add(
            AuditLog(
                organization_id=self.tenant.organization_id,
                actor_user_id=self.tenant.actor_user_id,
                action=action,
                entity_type="outbound_send_attempt",
                entity_id=attempt.id,
                metadata_json=metadata,
            )
        )

    @staticmethod
    def _normalize_email(value: str) -> str:
        clean = value.strip().lower()
        parsed = parseaddr(clean)[1]
        if parsed != clean or "@" not in clean or " " in clean or len(clean) > 320:
            raise ValueError("A valid business email is required")
        local, domain = clean.rsplit("@", 1)
        if not local or not domain or "." not in domain:
            raise ValueError("A valid business email is required")
        return clean
