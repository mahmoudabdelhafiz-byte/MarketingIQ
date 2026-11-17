from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketingiq.application.authorization import Permission, require_permission
from marketingiq.application.campaigns import (
    ALIGNED_ROLE_MATCHES,
    DEFAULT_CTA,
    CampaignDraftService,
)
from marketingiq.application.decision_makers import targeting_block_reason
from marketingiq.application.errors import ConflictError, NotFoundError
from marketingiq.application.languages import (
    AUTO,
    LANGUAGES,
    language_name,
    pack,
    resolve_language,
)
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.campaign_ai import (
    CampaignAIAttemptStatus,
    CampaignAIGenerationAttempt,
    CampaignAIProvider,
    CampaignAIProviderError,
    CampaignAIRequest,
    GroundingFact,
)
from marketingiq.domain.campaigns import CampaignChannel, CampaignDraft, CampaignDraftStatus
from marketingiq.domain.models import (
    AuditLog,
    Company,
    CompanyFact,
    DataClassification,
    ProductFitAssessment,
    RedistributionStatus,
)
from marketingiq.infrastructure.campaign_ai_provider import PROMPT_VERSION, request_fingerprint

WORKFLOW_VERSION = "grounded-ai-campaign-draft-v1"


PROMPT_FIELDS = frozenset(
    {
        "company_name", "product_name", "product_value_proposition", "value_proposition",
        "buyer_role", "call_to_action", "recipient_first_name", "language",
    }
)  # fmt: skip

class GroundedAICampaignService:
    """AI-assisted drafting constrained to approved MarketingIQ evidence and human review."""

    def __init__(
        self,
        session: Session,
        tenant: TenantContext,
        provider: CampaignAIProvider,
        now: datetime | None = None,
    ) -> None:
        self.session = session
        self.tenant = tenant
        self.provider = provider
        self.now = now or datetime.now(UTC)
        self.base = CampaignDraftService(session, tenant, now=self.now)

    def generate(
        self,
        relationship_id: str,
        qualification_id: str,
        contact_id: str,
        *,
        call_to_action: str | None = None,
        fallback_to_deterministic: bool = True,
        language: str | None = None,
    ) -> CampaignDraft:
        require_permission(self.tenant, Permission.GENERATE_CAMPAIGN_DRAFT)
        relationship = self.base._relationship(relationship_id)
        qualification = self.base._qualification(relationship, qualification_id)
        contact = self.base._contact(relationship, qualification, contact_id)
        product = self.base._product(qualification.product_id)

        if not (product.value_proposition or "").strip():
            raise ConflictError("CAMPAIGN_DRAFT_VALUE_PROPOSITION_REQUIRED")
        if contact.buyer_role_match not in ALIGNED_ROLE_MATCHES:
            raise ConflictError("CAMPAIGN_DRAFT_BUYER_ROLE_ALIGNMENT_REQUIRED")
        blocked = targeting_block_reason(contact)
        if blocked:
            raise ConflictError(f"CAMPAIGN_DRAFT_{blocked}")
        if not self.base._has_verified_email(contact.id):
            raise ConflictError("CAMPAIGN_DRAFT_VERIFIED_EMAIL_REQUIRED")

        company = self.session.get(Company, relationship.company_id)
        if company is None:
            raise NotFoundError("Company not found")
        try:
            draft_language = resolve_language(language, self.base.company_country(company))
        except ValueError as error:
            raise ConflictError("CAMPAIGN_DRAFT_LANGUAGE_UNSUPPORTED") from error
        cta = (call_to_action or pack(draft_language).default_cta).strip()
        self.base._validate_content("placeholder", "placeholder", cta, allow_placeholders=True)

        facts = self._approved_grounding_facts(qualification.fit_assessment_id)
        request = CampaignAIRequest(
            company_name=company.canonical_name,
            recipient_first_name=contact.first_name.strip() if contact.first_name else None,
            buyer_role=qualification.recommended_buyer_role,
            product_name=product.name,
            value_proposition=product.value_proposition.strip(),
            call_to_action=cta,
            grounding_facts=tuple(facts),
            language=draft_language,
            language_name=language_name(draft_language),
        )
        attempt = CampaignAIGenerationAttempt(
            organization_id=self.tenant.organization_id,
            organization_company_id=relationship.id,
            qualification_id=qualification.id,
            contact_id=contact.id,
            provider_key=self.provider.key,
            model=self.provider.model,
            prompt_version=PROMPT_VERSION,
            request_fingerprint=request_fingerprint(request),
            input_fact_keys=[fact.fact_key for fact in facts],
            status=CampaignAIAttemptStatus.PENDING.value,
            response_status="PENDING",
            requested_at=self.now,
            created_by_user_id=self.tenant.actor_user_id,
        )
        self.session.add(attempt)
        self.session.flush()

        try:
            result = self.provider.generate(request)
            self._validate_ai_result(
                result.subject, result.body, result.used_fact_keys, facts, draft_language
            )
        except CampaignAIProviderError as error:
            return self._fallback_or_raise(
                attempt,
                relationship_id,
                qualification_id,
                contact_id,
                cta,
                fallback_to_deterministic,
                error.category,
                draft_language,
                language,
            )
        except ValueError as error:
            return self._fallback_or_raise(
                attempt,
                relationship_id,
                qualification_id,
                contact_id,
                cta,
                fallback_to_deterministic,
                "GROUNDING_VALIDATION_FAILED",
                draft_language,
                language,
                detail=str(error)[:200],
            )

        draft = CampaignDraft(
            organization_id=self.tenant.organization_id,
            organization_company_id=relationship.id,
            company_id=relationship.company_id,
            product_id=product.id,
            qualification_id=qualification.id,
            contact_id=contact.id,
            channel=CampaignChannel.EMAIL,
            status=CampaignDraftStatus.DRAFT,
            subject=result.subject.strip(),
            body=result.body.strip(),
            call_to_action=cta,
            message_angle="AI_GROUNDED_ROLE_ALIGNMENT",
            personalization_snapshot={
                "company_id": relationship.company_id,
                "product_id": product.id,
                "qualification_id": qualification.id,
                "contact_id": contact.id,
                "recommended_buyer_role": qualification.recommended_buyer_role,
                "buyer_role_match": contact.buyer_role_match.value,
                "generation_mode": "AI_ASSISTED",
                "language": draft_language,
                "requested_language": language or AUTO,
            },
            evidence_snapshot={
                "fit_assessment_id": qualification.fit_assessment_id,
                "qualification_id": qualification.id,
                "grounding_fact_ids": [fact.fact_id for fact in facts],
                "grounding_fact_keys": [fact.fact_key for fact in facts],
                "used_fact_keys": list(result.used_fact_keys),
                "ai_provider": self.provider.key,
                "ai_model": result.model,
                "prompt_version": PROMPT_VERSION,
            },
            workflow_version=WORKFLOW_VERSION,
            created_by_user_id=self.tenant.actor_user_id,
            created_at=self.now,
            classification=DataClassification.MARKETINGIQ_DERIVED,
            redistribution_status=RedistributionStatus.INTERNAL_ONLY,
        )
        self.session.add(draft)
        self.session.flush()

        attempt.draft_id = draft.id
        attempt.status = CampaignAIAttemptStatus.SUCCESS.value
        attempt.response_status = "SUCCESS"
        attempt.input_tokens = result.input_tokens
        attempt.output_tokens = result.output_tokens
        attempt.completed_at = self.now
        self._audit(draft, attempt, "campaign_draft.ai_generated")
        self.session.flush()
        return draft

    def _approved_grounding_facts(self, assessment_id: str) -> list[GroundingFact]:
        assessment = self.session.scalar(
            select(ProductFitAssessment).where(
                ProductFitAssessment.id == assessment_id,
                ProductFitAssessment.organization_id == self.tenant.organization_id,
            )
        )
        if assessment is None:
            raise NotFoundError("Fit assessment not found")
        fact_ids = {
            str(item.get("selected_fact_id"))
            for item in (assessment.evidence_snapshot or [])
            if item.get("selected_fact_id")
        }
        if not fact_ids:
            return []
        facts = list(
            self.session.scalars(
                select(CompanyFact).where(
                    CompanyFact.id.in_(fact_ids),
                    CompanyFact.company_id == assessment.company_id,
                    CompanyFact.classification == DataClassification.PUBLIC_EVIDENCE,
                    CompanyFact.redistribution_status == RedistributionStatus.ALLOWED,
                )
            )
        )
        return sorted(
            [
                GroundingFact(
                    fact_id=fact.id,
                    fact_key=fact.fact_key,
                    value=_safe_fact_value(fact.value),
                )
                for fact in facts
            ],
            key=lambda item: (item.fact_key, item.fact_id),
        )

    def _validate_ai_result(
        self,
        subject: str,
        body: str,
        used_fact_keys: tuple[str, ...],
        facts: list[GroundingFact],
        language: str = "en",
    ) -> None:
        self.base._validate_content(subject, body, DEFAULT_CTA)
        if language == "ar" and not any("\u0600" <= char <= "\u06ff" for char in body):
            raise ValueError("AI output is not in the requested language")
        allowed = {fact.fact_key for fact in facts}
        # The model sometimes lists the prompt's own fields (company_name, ...) as "facts used".
        unknown = set(used_fact_keys) - allowed - PROMPT_FIELDS
        if unknown:
            raise ValueError(
                "AI referenced unsupported grounding keys: " + ", ".join(sorted(unknown))[:150]
            )
        forbidden = (
            "we know you are looking for",
            "your current project",
            "your budget",
            "urgent need",
            "guaranteed",
            "ميزانيتكم",
            "ميزانيتك",
            "مشروعكم الحالي",
            "حاجة عاجلة",
            "مضمون",
            "نضمن",
        )
        combined = f"{subject}\n{body}".lower()
        if any(marker in combined for marker in forbidden):
            raise ValueError("AI output contains an unsupported claim pattern")

    def _fallback_or_raise(
        self,
        attempt: CampaignAIGenerationAttempt,
        relationship_id: str,
        qualification_id: str,
        contact_id: str,
        cta: str,
        fallback: bool,
        category: str,
        draft_language: str = "en",
        requested_language: str | None = None,
        detail: str | None = None,
    ) -> CampaignDraft:
        attempt.error_category = category
        attempt.completed_at = self.now
        if not fallback:
            attempt.status = CampaignAIAttemptStatus.FAILED.value
            attempt.response_status = "FAILED"
            self.session.flush()
            raise ConflictError(f"CAMPAIGN_AI_{category}")
        attempt.status = CampaignAIAttemptStatus.FALLBACK.value
        attempt.response_status = "DETERMINISTIC_FALLBACK"
        attempt.fallback_used = True
        draft = self.base.generate(
            relationship_id,
            qualification_id,
            contact_id,
            # Languages without a reviewed template fall back to the English one.
            call_to_action=cta if draft_language in LANGUAGES else None,
            language=draft_language,
        )
        draft.personalization_snapshot = {
            **(draft.personalization_snapshot or {}),
            # Shown on the draft so a person knows why the template was used instead of AI.
            "ai_fallback_reason": category,
            **({"ai_fallback_detail": detail} if detail else {}),
            **({"requested_language": requested_language} if requested_language else {}),
        }
        attempt.draft_id = draft.id
        self.session.flush()
        return draft

    def _audit(
        self,
        draft: CampaignDraft,
        attempt: CampaignAIGenerationAttempt,
        action: str,
    ) -> None:
        self.session.add(
            AuditLog(
                organization_id=self.tenant.organization_id,
                actor_user_id=self.tenant.actor_user_id,
                action=action,
                entity_type="campaign_draft",
                entity_id=draft.id,
                metadata_json={
                    "provider_key": attempt.provider_key,
                    "model": attempt.model,
                    "prompt_version": attempt.prompt_version,
                    "grounding_fact_count": len(attempt.input_fact_keys or []),
                    "generation_attempt_id": attempt.id,
                },
            )
        )


def _safe_fact_value(value) -> str:
    if isinstance(value, str | int | float | bool):
        return str(value)[:500]
    if isinstance(value, list):
        return ", ".join(str(item) for item in value[:10])[:500]
    if isinstance(value, dict):
        return "; ".join(f"{key}: {val}" for key, val in list(value.items())[:10])[:500]
    return str(value)[:500]
