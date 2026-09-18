from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from marketingiq.application.authorization import Permission, require_permission
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.models import (
    AuditLog,
    CompanyFact,
    DataClassification,
    Evidence,
    HumanOverride,
    OrganizationCompany,
    RedistributionStatus,
    ReviewAction,
)


class StalenessStatus(StrEnum):
    FRESH = "FRESH"
    AGING = "AGING"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


class ConflictStatus(StrEnum):
    NONE = "NONE"
    LOW = "LOW"
    MATERIAL = "MATERIAL"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class ReviewStatus(StrEnum):
    UNREVIEWED = "UNREVIEWED"
    APPROVED = "APPROVED"
    OVERRIDDEN = "OVERRIDDEN"
    CONFLICT = "CONFLICT"
    STALE_REVIEW_REQUIRED = "STALE_REVIEW_REQUIRED"


# Days until stale. Aging begins at 75% of the interval.
DEFAULT_FRESHNESS_DAYS = {
    "active_hiring": 30,
    "employee_range": 180,
    "industry": 730,
    "country": 730,
    "headquarters": 365,
    "locations": 365,
    "company_description": 365,
}


@dataclass(frozen=True)
class ReviewRequest:
    action: ReviewAction
    selected_fact_id: str | None = None
    value: Any = None
    confidence: int = 100
    note: str = ""
    evidence_url: str | None = None


class IntelligenceService:
    def __init__(
        self,
        session: Session,
        tenant: TenantContext,
        freshness_days: dict[str, int] | None = None,
        now: datetime | None = None,
    ) -> None:
        self.session = session
        self.tenant = tenant
        self.freshness_days = {**DEFAULT_FRESHNESS_DAYS, **(freshness_days or {})}
        self.now = now or datetime.now(UTC)

    def _relationship(self, relationship_id: str) -> OrganizationCompany:
        require_permission(self.tenant, Permission.READ)
        item = self.session.scalar(
            select(OrganizationCompany).where(
                OrganizationCompany.id == relationship_id,
                OrganizationCompany.organization_id == self.tenant.organization_id,
            )
        )
        if item is None:
            raise LookupError("organization company not found")
        return item

    def history(self, relationship_id: str, fact_key: str) -> list[CompanyFact]:
        relationship = self._relationship(relationship_id)
        return list(
            self.session.scalars(
                select(CompanyFact)
                .options(selectinload(CompanyFact.evidences).selectinload(Evidence.source))
                .where(
                    CompanyFact.company_id == relationship.company_id,
                    CompanyFact.fact_key == fact_key,
                    or_(
                        CompanyFact.organization_id.is_(None),
                        CompanyFact.organization_id == self.tenant.organization_id,
                    ),
                )
                .order_by(CompanyFact.observed_at, CompanyFact.id)
            )
        )

    def list(self, relationship_id: str) -> list[dict[str, Any]]:
        relationship = self._relationship(relationship_id)
        keys = self.session.scalars(
            select(CompanyFact.fact_key)
            .where(
                CompanyFact.company_id == relationship.company_id,
                or_(
                    CompanyFact.organization_id.is_(None),
                    CompanyFact.organization_id == self.tenant.organization_id,
                ),
            )
            .distinct()
            .order_by(CompanyFact.fact_key)
        ).all()
        return [self.get(relationship_id, key) for key in keys]

    def get(self, relationship_id: str, fact_key: str) -> dict[str, Any]:
        facts = self.history(relationship_id, fact_key)
        if not facts:
            raise LookupError("fact not found")
        reviews = list(
            self.session.scalars(
                select(HumanOverride)
                .join(CompanyFact, HumanOverride.company_fact_id == CompanyFact.id)
                .where(
                    HumanOverride.organization_id == self.tenant.organization_id,
                    HumanOverride.fact_key == fact_key,
                    CompanyFact.company_id == facts[0].company_id,
                    HumanOverride.revoked_at.is_(None),
                )
                .order_by(HumanOverride.created_at.desc(), HumanOverride.id.desc())
            )
        )
        active = reviews[0] if reviews else None
        by_id = {fact.id: fact for fact in facts}
        if active and active.company_fact_id in by_id:
            selected = by_id[active.company_fact_id]
            reason = f"HUMAN_{active.action.value}"
        else:
            selected = max(facts, key=self._rank)
            reason = self._automatic_reason(selected)
        stale = self._staleness(selected)
        conflict = self._conflict(facts)
        if active and active.action == ReviewAction.RESOLVE_CONFLICT:
            conflict = ConflictStatus.NONE
        if active:
            review = (
                ReviewStatus.APPROVED
                if active.action == ReviewAction.APPROVE
                else ReviewStatus.OVERRIDDEN
            )
        elif conflict in {ConflictStatus.MATERIAL, ConflictStatus.NEEDS_REVIEW}:
            review = ReviewStatus.CONFLICT
        elif stale == StalenessStatus.STALE:
            review = ReviewStatus.STALE_REVIEW_REQUIRED
        else:
            review = ReviewStatus.UNREVIEWED
        evidence = [self._safe_evidence(item, selected) for item in selected.evidences]
        verified = max(
            (item.last_verified_at for item in selected.evidences if item.last_verified_at),
            default=None,
        )
        return {
            "fact_key": fact_key,
            "selected_fact_id": selected.id,
            "value": selected.value,
            "classification": selected.classification,
            "redistribution_status": selected.redistribution_status,
            "confidence": selected.confidence,
            "observed_at": selected.observed_at,
            "last_verified_at": verified,
            "source": evidence[0]["source"] if evidence else None,
            "research_run_id": selected.research_run_id,
            "selection_reason": reason,
            "staleness_status": stale,
            "conflict_status": conflict,
            "review_status": review,
            "evidence_summary": evidence,
            "observation_count": len(facts),
        }

    def review(self, relationship_id: str, fact_key: str, request: ReviewRequest) -> dict[str, Any]:
        require_permission(self.tenant, Permission.REVIEW_INTELLIGENCE)
        relationship = self._relationship(relationship_id)
        if request.action == ReviewAction.MANUAL_CORRECTION:
            if request.value is None:
                raise ValueError("manual correction requires a value")
            fact = CompanyFact(
                company_id=relationship.company_id,
                organization_id=self.tenant.organization_id,
                fact_key=fact_key,
                value=request.value,
                classification=DataClassification.CUSTOMER_PROVIDED,
                redistribution_status=RedistributionStatus.INTERNAL_ONLY,
                confidence=request.confidence,
                observed_at=self.now,
            )
            if request.evidence_url:
                from marketingiq.application.companies import validate_reference_url

                fact.evidences.append(
                    Evidence(reference_url=validate_reference_url(request.evidence_url))
                )
            self.session.add(fact)
            self.session.flush()
            selected = fact
        else:
            facts = self.history(relationship_id, fact_key)
            selected = next((f for f in facts if f.id == request.selected_fact_id), None)
            if selected is None:
                if request.action == ReviewAction.APPROVE and request.selected_fact_id is None:
                    selected_id = self.get(relationship_id, fact_key)["selected_fact_id"]
                    selected = next(f for f in facts if f.id == selected_id)
                else:
                    raise ValueError(
                        "selected_fact_id must identify an allowed existing observation"
                    )
        for prior in self._active_reviews(fact_key, relationship.company_id):
            prior.revoked_at = self.now
            prior.revoked_by_user_id = self.tenant.actor_user_id
        override = HumanOverride(
            organization_id=self.tenant.organization_id,
            company_fact_id=selected.id,
            fact_key=fact_key,
            user_id=self.tenant.actor_user_id,
            action=request.action,
            replacement_value=selected.value,
            reason=request.note or request.action.value,
        )
        self.session.add(override)
        self.session.flush()
        self._audit("intelligence.reviewed", override.id, {"action": request.action.value})
        return self.get(relationship_id, fact_key)

    def revoke(self, relationship_id: str, fact_key: str, note: str = "") -> dict[str, Any]:
        require_permission(self.tenant, Permission.REVIEW_INTELLIGENCE)
        relationship = self._relationship(relationship_id)
        active = self._active_reviews(fact_key, relationship.company_id)
        if not active:
            raise LookupError("active review not found")
        for item in active:
            item.revoked_at = self.now
            item.revoked_by_user_id = self.tenant.actor_user_id
        self._audit("intelligence.review_revoked", active[0].id, {"note": note})
        self.session.flush()
        return self.get(relationship_id, fact_key)

    def _active_reviews(self, fact_key: str, company_id: str) -> list[HumanOverride]:
        return list(
            self.session.scalars(
                select(HumanOverride)
                .join(CompanyFact, HumanOverride.company_fact_id == CompanyFact.id)
                .where(
                    HumanOverride.organization_id == self.tenant.organization_id,
                    HumanOverride.fact_key == fact_key,
                    HumanOverride.revoked_at.is_(None),
                    CompanyFact.company_id == company_id,
                )
            )
        )

    def _rank(self, fact: CompanyFact) -> tuple[Any, ...]:
        precedence = {
            DataClassification.CUSTOMER_PROVIDED: 3,
            DataClassification.PUBLIC_EVIDENCE: 2,
            DataClassification.THIRD_PARTY_LICENSED: 1,
            DataClassification.MARKETINGIQ_DERIVED: 0,
        }
        verified = max(
            (self._aware(e.last_verified_at) for e in fact.evidences if e.last_verified_at),
            default=datetime.min.replace(tzinfo=UTC),
        )
        return (
            precedence[fact.classification],
            fact.confidence,
            verified,
            self._aware(fact.observed_at),
            fact.id,
        )

    def _automatic_reason(self, selected: CompanyFact) -> str:
        return f"POLICY_{selected.classification.value}_CONFIDENCE_FRESHNESS"

    def _staleness(self, fact: CompanyFact) -> StalenessStatus:
        if fact.observed_at is None:
            return StalenessStatus.UNKNOWN
        days = self.freshness_days.get(fact.fact_key, 365)
        age = (self.now - self._aware(fact.observed_at)).total_seconds() / 86400
        if age > days:
            return StalenessStatus.STALE
        if age > days * 0.75:
            return StalenessStatus.AGING
        return StalenessStatus.FRESH

    def _conflict(self, facts: list[CompanyFact]) -> ConflictStatus:
        credible = [f for f in facts if f.confidence >= 60]
        distinct = {json.dumps(f.value, sort_keys=True, default=str).casefold() for f in credible}
        if len(distinct) <= 1:
            return ConflictStatus.NONE
        if len(credible) >= 2 and all(f.confidence >= 80 for f in credible):
            return ConflictStatus.NEEDS_REVIEW
        return ConflictStatus.MATERIAL if len(credible) >= 2 else ConflictStatus.LOW

    def _safe_evidence(self, evidence: Evidence, fact: CompanyFact) -> dict[str, Any]:
        safe_url = (
            evidence.reference_url
            if fact.redistribution_status == RedistributionStatus.ALLOWED
            and fact.classification != DataClassification.THIRD_PARTY_LICENSED
            else None
        )
        return {
            "source": evidence.source.display_name if evidence.source else "Manual evidence",
            "source_url": safe_url,
            "retrieved_at": evidence.retrieved_at,
            "last_verified_at": evidence.last_verified_at,
        }

    def _audit(self, action: str, entity_id: str, metadata: dict[str, Any]) -> None:
        self.session.add(
            AuditLog(
                organization_id=self.tenant.organization_id,
                actor_user_id=self.tenant.actor_user_id,
                action=action,
                entity_type="human_override",
                entity_id=entity_id,
                metadata_json=metadata,
            )
        )

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=UTC)
