from __future__ import annotations

import os
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketingiq.application.authorization import Permission, require_permission
from marketingiq.application.errors import ConflictError, NotFoundError
from marketingiq.application.fit_freshness import FitAssessmentFreshnessService
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.models import (
    AuditLog,
    BuyerRoleMatch,
    CompanyIdentifier,
    ContactCandidate,
    ContactEmail,
    DataClassification,
    EmailVerificationStatus,
    LeadQualification,
    OrganizationCompany,
    ProviderUsage,
    QualificationStatus,
    RedistributionStatus,
)
from marketingiq.domain.providers import ProviderError


def match_buyer_role(target: str, title: str | None, department: str | None, seniority: str | None):
    def normalize(value: str | None) -> set[str]:
        return set(re.findall(r"[a-z0-9]+", (value or "").lower()))

    wanted, actual = normalize(target), normalize(title)
    if wanted and wanted == actual:
        return BuyerRoleMatch.EXACT, "Normalized title exactly matches the recommended buyer role"
    overlap = wanted & actual
    if wanted and len(overlap) / len(wanted) >= 2 / 3:
        return BuyerRoleMatch.STRONG, "Most recommended-role terms occur in the title"
    if overlap or (normalize(department) & wanted) or (normalize(seniority) & wanted):
        return BuyerRoleMatch.PARTIAL, "Title, department, or seniority partially aligns"
    return BuyerRoleMatch.UNKNOWN, "Provider fields do not establish role alignment"


class ContactDiscoveryService:
    def __init__(self, session: Session, tenant: TenantContext, registry, now=None):
        self.session, self.tenant, self.registry = session, tenant, registry
        self.now = now or datetime.now(UTC)
        self.search_ttl = timedelta(hours=int(os.getenv("CONTACT_SEARCH_FRESHNESS_HOURS", "168")))
        self.find_ttl = timedelta(hours=int(os.getenv("EMAIL_FIND_FRESHNESS_HOURS", "720")))
        self.verify_ttl = timedelta(hours=int(os.getenv("EMAIL_VERIFY_FRESHNESS_HOURS", "168")))

    def _relationship(self, relationship_id):
        item = self.session.scalar(
            select(OrganizationCompany).where(
                OrganizationCompany.id == relationship_id,
                OrganizationCompany.organization_id == self.tenant.organization_id,
            )
        )
        if not item:
            raise NotFoundError("Company relationship not found")
        return item

    def _qualification(self, rel, qualification_id):
        item = self.session.scalar(
            select(LeadQualification).where(
                LeadQualification.id == qualification_id,
                LeadQualification.organization_id == self.tenant.organization_id,
                LeadQualification.organization_company_id == rel.id,
                LeadQualification.company_id == rel.company_id,
            )
        )
        if not item:
            raise NotFoundError("Lead qualification not found")
        if item.status not in {QualificationStatus.HIGH_PRIORITY, QualificationStatus.QUALIFIED}:
            raise ConflictError(
                "CONTACT_DISCOVERY_NOT_QUALIFIED: qualification is not suitable for pursuit"
            )
        assessment = FitAssessmentFreshnessService(self.session, self.tenant, self.now).get(
            rel.id, item.fit_assessment_id
        )
        if not assessment["is_current"]:
            raise ConflictError(
                "CONTACT_DISCOVERY_REQUALIFICATION_REQUIRED: fit assessment is stale"
            )
        if not item.recommended_buyer_role.strip():
            raise ConflictError("CONTACT_DISCOVERY_BUYER_ROLE_REQUIRED")
        return item

    def _domain(self, company_id):
        domain = self.session.scalar(
            select(CompanyIdentifier.normalized_value).where(
                CompanyIdentifier.company_id == company_id,
                CompanyIdentifier.kind == "DOMAIN",
                CompanyIdentifier.is_primary.is_(True),
            )
        )
        if not domain:
            raise ConflictError("CONTACT_DISCOVERY_DOMAIN_REQUIRED")
        return domain

    def _usage(self, provider, company_id, operation, ttl, force):
        usage = ProviderUsage(
            provider_key=provider.key,
            organization_id=self.tenant.organization_id,
            company_id=company_id,
            operation=operation,
            requested_at=self.now,
        )
        self.session.add(usage)
        self.session.flush()
        cached = (
            None
            if force
            else self.session.scalar(
                select(ProviderUsage)
                .where(
                    ProviderUsage.id != usage.id,
                    ProviderUsage.provider_key == provider.key,
                    ProviderUsage.organization_id == self.tenant.organization_id,
                    ProviderUsage.company_id == company_id,
                    ProviderUsage.operation == operation,
                    ProviderUsage.success.is_(True),
                    ProviderUsage.completed_at >= self.now - ttl,
                )
                .order_by(ProviderUsage.completed_at.desc())
            )
        )
        if cached:
            usage.cache_hit, usage.success, usage.response_status = True, True, "CACHED"
            usage.credits_used, usage.completed_at = 0, self.now
        return usage, cached

    def discover(
        self,
        relationship_id,
        qualification_id,
        provider_key="HUNTER",
        max_results=10,
        force_refresh=False,
    ):
        require_permission(self.tenant, Permission.SPEND_PROVIDER_CREDITS)
        rel = self._relationship(relationship_id)
        qualification = self._qualification(rel, qualification_id)
        provider = self.registry.get(provider_key)
        usage, cached = self._usage(
            provider, rel.company_id, "CONTACT_SEARCH", self.search_ttl, force_refresh
        )
        if not cached:
            try:
                result = provider.search_contacts(self._domain(rel.company_id), max_results)
                usage.success, usage.response_status = (
                    True,
                    "SUCCESS" if result.contacts else "NO_RESULTS",
                )
                usage.request_identifier, usage.credits_used, usage.credits_remaining = (
                    result.request_identifier,
                    result.credits_used,
                    result.credits_remaining,
                )
                for found in result.contacts:
                    match, reason = match_buyer_role(
                        qualification.recommended_buyer_role,
                        found.job_title,
                        found.department,
                        found.seniority,
                    )
                    existing = None
                    if found.reference:
                        existing = self.session.scalar(
                            select(ContactCandidate).where(
                                ContactCandidate.organization_id == self.tenant.organization_id,
                                ContactCandidate.provider_key == provider.key,
                                ContactCandidate.provider_contact_reference == found.reference,
                            )
                        )
                    elif found.email:
                        existing = self.session.scalar(
                            select(ContactCandidate)
                            .join(ContactEmail)
                            .where(
                                ContactCandidate.organization_id
                                == self.tenant.organization_id,
                                ContactCandidate.company_id == rel.company_id,
                                ContactEmail.email == found.email.strip().lower(),
                            )
                        )
                    if existing:
                        continue
                    contact = ContactCandidate(
                        organization_id=self.tenant.organization_id,
                        organization_company_id=rel.id,
                        company_id=rel.company_id,
                        qualification_id=qualification.id,
                        provider_key=provider.key,
                        provider_contact_reference=found.reference,
                        first_name=found.first_name,
                        last_name=found.last_name,
                        full_name=found.full_name,
                        job_title=found.job_title,
                        department=found.department,
                        seniority=found.seniority,
                        normalized_buyer_role=qualification.recommended_buyer_role.strip().lower(),
                        buyer_role_match=match,
                        buyer_role_match_reason=reason,
                        confidence=found.confidence,
                        discovered_at=self.now,
                        classification=DataClassification.THIRD_PARTY_LICENSED,
                        redistribution_status=RedistributionStatus.RESTRICTED,
                    )
                    self.session.add(contact)
                    self.session.flush()
                    if found.email:
                        self._add_email(contact, found.email, provider.key)
            except ProviderError as error:
                usage.response_status, usage.error_category = "FAILURE", error.category
                usage.completed_at = self.now
                self.session.commit()
                raise
            finally:
                usage.completed_at = self.now
        self._audit(
            "contact.discovery",
            rel.company_id,
            qualification.id,
            None,
            provider.key,
            usage.response_status,
            qualification.recommended_buyer_role,
        )
        return self.list(relationship_id)

    def list(self, relationship_id):
        require_permission(self.tenant, Permission.READ)
        self._relationship(relationship_id)
        return list(
            self.session.scalars(
                select(ContactCandidate)
                .where(
                    ContactCandidate.organization_id == self.tenant.organization_id,
                    ContactCandidate.organization_company_id == relationship_id,
                )
                .order_by(ContactCandidate.discovered_at.desc())
            )
        )

    def get(self, relationship_id, contact_id):
        require_permission(self.tenant, Permission.READ)
        self._relationship(relationship_id)
        item = self.session.scalar(
            select(ContactCandidate).where(
                ContactCandidate.id == contact_id,
                ContactCandidate.organization_id == self.tenant.organization_id,
                ContactCandidate.organization_company_id == relationship_id,
            )
        )
        if not item:
            raise NotFoundError("Contact not found")
        return item

    def _add_email(self, contact, email, provider_key):
        normalized = email.strip().lower()
        existing = self.session.scalar(
            select(ContactEmail).where(
                ContactEmail.contact_id == contact.id, ContactEmail.email == normalized
            )
        )
        if existing:
            return existing
        customer = provider_key == "CUSTOMER"
        channel = ContactEmail(
            contact_id=contact.id,
            email=normalized,
            source_provider=provider_key,
            verification_status=EmailVerificationStatus.UNKNOWN,
            found_at=self.now,
            classification=(
                DataClassification.CUSTOMER_PROVIDED
                if customer
                else DataClassification.THIRD_PARTY_LICENSED
            ),
            redistribution_status=(
                RedistributionStatus.INTERNAL_ONLY
                if customer
                else RedistributionStatus.RESTRICTED
            ),
        )
        self.session.add(channel)
        self.session.flush()
        return channel

    def find_email(self, relationship_id, contact_id, provider_key="HUNTER", force_refresh=False):
        require_permission(self.tenant, Permission.SPEND_PROVIDER_CREDITS)
        contact = self.get(relationship_id, contact_id)
        recent = self.session.scalar(
            select(ContactEmail)
            .where(
                ContactEmail.contact_id == contact.id,
                ContactEmail.found_at >= self.now - self.find_ttl,
            )
            .order_by(ContactEmail.found_at.desc())
        )
        provider = self.registry.get(provider_key)
        if recent and not force_refresh:
            usage, _ = self._usage(
                provider, contact.company_id, "EMAIL_FIND", self.find_ttl, True
            )
            usage.cache_hit = True
            usage.success = True
            usage.response_status = "CACHED"
            usage.credits_used = 0
            usage.completed_at = self.now
        else:
            if not contact.first_name or not contact.last_name:
                raise ConflictError("EMAIL_FIND_NAME_REQUIRED")
            domain = self._domain(contact.company_id)
            usage, _ = self._usage(
                provider, contact.company_id, "EMAIL_FIND", self.find_ttl, True
            )
            try:
                result = provider.find_email(
                    {
                        "domain": domain,
                        "first_name": contact.first_name,
                        "last_name": contact.last_name,
                    }
                )
                usage.success = True
                usage.response_status = "SUCCESS" if result.contacts else "NO_RESULTS"
                usage.credits_used = result.credits_used
                usage.credits_remaining = result.credits_remaining
                usage.request_identifier = result.request_identifier
                if result.contacts and result.contacts[0].email:
                    self._add_email(contact, result.contacts[0].email, provider.key)
            except ProviderError as error:
                usage.response_status = "FAILURE"
                usage.error_category = error.category
                usage.completed_at = self.now
                self.session.commit()
                raise
            finally:
                usage.completed_at = self.now
        self._audit(
            "contact.email_found",
            contact.company_id,
            contact.qualification_id,
            contact.id,
            provider.key,
            usage.response_status,
            contact.normalized_buyer_role,
        )
        return contact

    def verify_email(
        self, relationship_id, contact_id, email=None, provider_key="HUNTER", force_refresh=False
    ):
        require_permission(self.tenant, Permission.SPEND_PROVIDER_CREDITS)
        contact = self.get(relationship_id, contact_id)
        channel = (
            self._add_email(contact, email, "CUSTOMER")
            if email
            else (contact.channels[0] if contact.channels else None)
        )
        if not channel:
            raise ConflictError("EMAIL_REQUIRED")
        provider = self.registry.get(provider_key)
        # Verification freshness is channel-specific; the channel timestamp is the cache key.
        usage, cached = self._usage(
            provider, contact.company_id, "EMAIL_VERIFY", self.verify_ttl, True
        )
        if (
            channel.verified_at
            and channel.verified_at >= self.now - self.verify_ttl
            and not force_refresh
        ):
            cached = channel
            usage.cache_hit = True
            usage.success = True
            usage.response_status = "CACHED"
            usage.credits_used = 0
            usage.completed_at = self.now
        if not cached:
            try:
                result = provider.verify_email(channel.email)
                found = result.contacts[0] if result.contacts else None
                raw = (found.reference or "unknown").lower() if found else "unknown"
                mapping = {
                    "valid": EmailVerificationStatus.VALID,
                    "invalid": EmailVerificationStatus.INVALID,
                    "accept_all": EmailVerificationStatus.ACCEPT_ALL,
                    "risky": EmailVerificationStatus.RISKY,
                    "webmail": EmailVerificationStatus.RISKY,
                    "disposable": EmailVerificationStatus.RISKY,
                    "unknown": EmailVerificationStatus.UNVERIFIABLE,
                }
                channel.verification_status = mapping.get(raw, EmailVerificationStatus.UNVERIFIABLE)
                channel.verification_score = found.confidence if found else None
                channel.verified_at = self.now
                contact.last_verified_at = self.now
                usage.success = True
                usage.response_status = "SUCCESS"
                usage.credits_used = result.credits_used
                usage.credits_remaining = result.credits_remaining
                usage.request_identifier = result.request_identifier
            except ProviderError as error:
                usage.response_status = "FAILURE"
                usage.error_category = error.category
                usage.completed_at = self.now
                self.session.commit()
                raise
            finally:
                usage.completed_at = self.now
        self._audit(
            "contact.email_verified",
            contact.company_id,
            contact.qualification_id,
            contact.id,
            provider.key,
            usage.response_status,
            contact.normalized_buyer_role,
        )
        return contact

    def _audit(self, action, company_id, qualification_id, contact_id, provider, result, role):
        self.session.add(
            AuditLog(
                organization_id=self.tenant.organization_id,
                actor_user_id=self.tenant.actor_user_id,
                action=action,
                entity_type="contact_candidate",
                entity_id=contact_id or qualification_id,
                metadata_json={
                    "company_id": company_id,
                    "qualification_id": qualification_id,
                    "contact_id": contact_id,
                    "provider": provider,
                    "operation": action,
                    "result_category": result,
                    "buyer_role": role,
                },
            )
        )
