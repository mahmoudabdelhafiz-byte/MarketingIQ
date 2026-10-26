"""Decision-maker discovery: find the right people at a qualified company, with evidence.

Order of work (cheapest first):
1. The company's own leadership/team pages (free, public).
2. Rule-based role resolution; one batched AI call only for ambiguous titles (if configured).
3. One filtered Hunter Domain Search — only when provider credits are explicitly allowed.
4. Email Finder / Verifier — only for the top-ranked people per persona, within a hard cap.

Nothing is invented: every person, title, profile link and email comes from a stored source,
and emails are never constructed from patterns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from marketingiq.application.authorization import Permission, require_permission
from marketingiq.application.contacts import ContactDiscoveryService
from marketingiq.application.decision_maker_scoring import (
    RANKABLE,
    STATUS_ORDER,
    SUITABILITY_ORDER,
    EmailView,
    EvidenceView,
    best_email,
    contact_status,
    email_state,
    employment_status,
    legacy_role_match,
    normalize_linkedin,
    parse_provider_date,
    suitability,
)
from marketingiq.application.errors import ConflictError, NotFoundError
from marketingiq.application.role_resolver import (
    PersonaSpec,
    PersonCandidate,
    RoleResolver,
    normalize_text,
)
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.campaigns import CampaignDraft
from marketingiq.domain.decision_makers import (
    ContactEvidence,
    ContactStatus,
    DiscoveryOutcome,
    EmailOrigin,
    EmploymentStatus,
    EvidenceSourceType,
    ProductPersona,
    ReviewStatus,
    RoleMatch,
    Suitability,
)
from marketingiq.domain.models import (
    AuditLog,
    Company,
    ContactCandidate,
    ContactEmail,
    DataClassification,
    EmailVerificationStatus,
    LeadQualification,
    OrganizationCompany,
    Product,
    RedistributionStatus,
)
from marketingiq.domain.providers import ProviderError

WORKFLOW_VERSION = "decision-maker-discovery-v1"
RUN_ACTION = "decision_makers.discovered"
MAX_PERSONAS = 12
MAX_EMAIL_OPERATIONS = 12
HUNTER_SEARCH_LIMIT = 25
EMPLOYMENT_ORDER = {
    EmploymentStatus.CURRENT_CONFIRMED: 0,
    EmploymentStatus.CURRENT_LIKELY: 1,
    EmploymentStatus.CURRENT_UNCERTAIN: 2,
}
VERIFY_MAP = {
    "valid": EmailVerificationStatus.VALID,
    "invalid": EmailVerificationStatus.INVALID,
    "accept_all": EmailVerificationStatus.ACCEPT_ALL,
    "risky": EmailVerificationStatus.RISKY,
    "webmail": EmailVerificationStatus.RISKY,
    "disposable": EmailVerificationStatus.RISKY,
    "unknown": EmailVerificationStatus.UNVERIFIABLE,
}


_DEFAULT = object()


class _Operator:
    """Minimal provider identity for usage rows that have no registry provider (e.g. OpenAI)."""

    def __init__(self, key: str) -> None:
        self.key = key


@dataclass
class Observation:
    source: str
    provider_key: str
    full_name: str | None
    first_name: str | None = None
    last_name: str | None = None
    reference: str | None = None
    title: str | None = None
    position_raw: str | None = None
    department: str | None = None
    seniority: str | None = None
    email: str | None = None
    email_confidence: int | None = None
    email_verification: str | None = None
    email_verification_date: str | None = None
    linkedin: str | None = None
    decision_maker: bool | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class RunReport:
    public_pages_fetched: int = 0
    public_people_found: int = 0
    public_cached: bool = False
    ai_calls: int = 0
    ai_errors: list[str] = field(default_factory=list)
    hunter_searches: int = 0
    hunter_cached: bool = False
    hunter_people_found: int = 0
    email_finds: int = 0
    email_verifications: int = 0
    credits_used: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "public": {
                "pages_fetched": self.public_pages_fetched,
                "people_found": self.public_people_found,
                "cached": self.public_cached,
            },
            "openai": {"calls": self.ai_calls, "errors": self.ai_errors},
            "provider_credits": {
                "hunter_searches": self.hunter_searches,
                "hunter_cached": self.hunter_cached,
                "hunter_people_found": self.hunter_people_found,
                "email_finds": self.email_finds,
                "credits_used": self.credits_used,
            },
            "verification": {"email_verifications": self.email_verifications},
            "notes": self.notes,
        }


EVIDENCE_LIMITS = {
    "provider_key": 100,
    "provider_reference": 255,
    "source_url": 2048,
    "claimed_name": 220,
    "claimed_title": 255,
    "claimed_company": 255,
    "snippet": 500,
}


def evidence_row(organization_id: str, contact_id: str, item: dict[str, Any]) -> ContactEvidence:
    """Build an evidence row, clipping text to the column sizes (MariaDB rejects overflow)."""

    values = dict(item)
    values["source_type"] = str(values["source_type"])
    for key, limit in EVIDENCE_LIMITS.items():
        if isinstance(values.get(key), str):
            values[key] = values[key][:limit]
    confidence = values.get("confidence")
    values["confidence"] = max(0, min(int(confidence), 100)) if confidence is not None else None
    return ContactEvidence(organization_id=organization_id, contact_id=contact_id, **values)


def _verbatim(value: str, haystack: str) -> bool:
    """Whole-word, case-insensitive presence of the exact value in the page text."""

    pattern = r"(?<!\w)" + re.escape(value.lower()) + r"(?!\w)"
    return re.search(pattern, haystack) is not None


def _http_url(value: str | None) -> str | None:
    if value and re.match(r"^https?://", value.strip(), re.I):
        return value.strip()
    return None


def split_name(full_name: str) -> tuple[str | None, str | None]:
    tokens = full_name.split()
    if len(tokens) < 2:
        return (tokens[0] if tokens else None), None
    return tokens[0], " ".join(tokens[1:])


def name_key(value: str | None) -> str:
    return " ".join(re.findall(r"[a-z]+", (value or "").lower()))


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class DecisionMakerService:
    def __init__(
        self,
        session: Session,
        tenant: TenantContext,
        registry,
        *,
        ai_client=_DEFAULT,
        page_scanner=_DEFAULT,
        now: datetime | None = None,
    ) -> None:
        self.session, self.tenant, self.registry = session, tenant, registry
        self.now = now or datetime.now(UTC)
        self.contacts = ContactDiscoveryService(session, tenant, registry, now=self.now)
        if ai_client is _DEFAULT:
            from marketingiq.infrastructure.decision_maker_ai import (
                build_decision_maker_ai_client,
            )

            ai_client = build_decision_maker_ai_client()
        if page_scanner is _DEFAULT:
            from marketingiq.infrastructure.leadership_pages import LeadershipPageScanner

            public = self._provider("PUBLIC_WEB")
            page_scanner = LeadershipPageScanner(public) if public is not None else None
        self.ai_client = ai_client
        self.page_scanner = page_scanner

    # ------------------------------------------------------------------ personas
    def _product(self, product_id: str) -> Product:
        product = self.session.scalar(
            select(Product).where(
                Product.id == product_id, Product.organization_id == self.tenant.organization_id
            )
        )
        if product is None:
            raise NotFoundError("Product not found")
        return product

    def list_personas(self, product_id: str) -> dict[str, Any]:
        require_permission(self.tenant, Permission.READ)
        product = self._product(product_id)
        rows = self._persona_rows(product.id)
        return {
            "product_id": product.id,
            "personas": [persona_output(p) for p in rows],
            "derived_from_buyer_roles": not any(p.is_active for p in rows),
            "effective": [spec_output(s) for s in self.persona_specs(product)],
        }

    def _persona_rows(self, product_id: str) -> list[ProductPersona]:
        return list(
            self.session.scalars(
                select(ProductPersona)
                .where(
                    ProductPersona.organization_id == self.tenant.organization_id,
                    ProductPersona.product_id == product_id,
                )
                .order_by(ProductPersona.priority, ProductPersona.name)
            )
        )

    def has_configured_personas(self, product_id: str) -> bool:
        return any(p.is_active for p in self._persona_rows(product_id))

    def persona_specs(self, product: Product) -> list[PersonaSpec]:
        rows = [p for p in self._persona_rows(product.id) if p.is_active]
        if rows:
            return [
                PersonaSpec(
                    id=p.id,
                    name=p.name,
                    titles=tuple(p.titles or ()),
                    departments=tuple(p.departments or ()),
                    seniorities=tuple(p.seniorities or ()),
                    max_contacts=max(1, min(int(p.max_contacts or 2), 3)),
                    priority=int(p.priority or 100),
                )
                for p in rows
            ]
        # Fall back to the product's own buyer roles — nothing is hard-coded here.
        roles = [
            *[r for r in (product.primary_buyer_roles or []) if str(r).strip()],
            *[r for r in (product.secondary_buyer_roles or []) if str(r).strip()],
        ]
        specs: list[PersonaSpec] = []
        seen: set[str] = set()
        for index, role in enumerate(roles):
            role = str(role).strip()[:120]
            if role.lower() in seen:
                continue
            seen.add(role.lower())
            specs.append(
                PersonaSpec(id=None, name=role, titles=(role,), max_contacts=2, priority=index + 1)
            )
        return specs[:MAX_PERSONAS]

    def replace_personas(self, product_id: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        require_permission(self.tenant, Permission.WRITE_CATALOG)
        product = self._product(product_id)
        if len(items) > MAX_PERSONAS:
            raise ConflictError("PERSONAS_TOO_MANY")
        cleaned: list[dict[str, Any]] = []
        names: set[str] = set()
        for index, item in enumerate(items):
            name = re.sub(r"\s+", " ", str(item.get("name") or "")).strip()[:120]
            if not name:
                raise ConflictError("PERSONA_NAME_REQUIRED")
            if name.lower() in names:
                raise ConflictError("PERSONA_NAME_DUPLICATE")
            names.add(name.lower())

            def clean_list(values, limit, max_len=120):
                out: list[str] = []
                for value in values or []:
                    text = re.sub(r"\s+", " ", str(value)).strip()[:max_len]
                    if text and text.lower() not in [x.lower() for x in out]:
                        out.append(text)
                return out[:limit]

            cleaned.append(
                {
                    "id": item.get("id"),
                    "name": name,
                    "titles": clean_list(item.get("titles"), 30),
                    "departments": clean_list(item.get("departments"), 10, 60),
                    "seniorities": clean_list(item.get("seniorities"), 10, 60),
                    "max_contacts": max(1, min(int(item.get("max_contacts") or 2), 3)),
                    "priority": max(1, min(int(item.get("priority") or index + 1), 1000)),
                    "is_active": bool(item.get("is_active", True)),
                }
            )
        existing = self._persona_rows(product.id)
        by_id = {p.id: p for p in existing}
        by_name = {p.name.lower(): p for p in existing}
        matched: list[tuple[dict[str, Any], ProductPersona | None]] = []
        used: set[str] = set()
        for data in cleaned:
            row = by_id.get(data["id"]) if data["id"] else None
            if row is None:
                row = by_name.get(data["name"].lower())
            if row is not None and row.id in used:
                row = None
            if row is not None:
                used.add(row.id)
            matched.append((data, row))
        removed = [p for p in existing if p.id not in used]
        try:
            # Delete first, then park kept names, so renames/swaps never collide.
            for row in removed:
                self.session.delete(row)
            self.session.flush()
            for _, row in matched:
                if row is not None:
                    row.name = f"~{row.id}"[:120]
            self.session.flush()
            for data, row in matched:
                if row is None:
                    row = ProductPersona(
                        organization_id=self.tenant.organization_id, product_id=product.id
                    )
                    self.session.add(row)
                for key in ("name", "titles", "departments", "seniorities", "max_contacts"):
                    setattr(row, key, data[key])
                row.priority, row.is_active = data["priority"], data["is_active"]
            self.session.flush()
        except IntegrityError:
            raise ConflictError("PERSONA_NAME_DUPLICATE") from None
        self.session.add(
            AuditLog(
                organization_id=self.tenant.organization_id,
                actor_user_id=self.tenant.actor_user_id,
                action="product.personas.updated",
                entity_type="product",
                entity_id=product.id,
                metadata_json={
                    "persona_names": [d["name"] for d in cleaned],
                    "removed": [p.name for p in removed],
                },
            )
        )
        self.session.flush()
        return self.list_personas(product.id)

    # ------------------------------------------------------------------ helpers
    def _relationship(self, relationship_id: str) -> OrganizationCompany:
        return self.contacts._relationship(relationship_id)

    def _latest_qualification(self, rel, product_id: str | None) -> LeadQualification:
        query = select(LeadQualification).where(
            LeadQualification.organization_id == self.tenant.organization_id,
            LeadQualification.organization_company_id == rel.id,
        )
        if product_id:
            query = query.where(LeadQualification.product_id == product_id)
        item = self.session.scalar(
            query.order_by(LeadQualification.qualified_at.desc(), LeadQualification.id.desc())
        )
        if item is None:
            raise ConflictError(
                "DECISION_MAKERS_NOT_QUALIFIED: qualify this company for the product first"
            )
        # Reuse the existing gate: actionable status, current fit, recommended buyer role.
        try:
            return self.contacts._qualification(rel, item.id)
        except ConflictError as error:
            raise ConflictError(
                str(error).replace("CONTACT_DISCOVERY_", "DECISION_MAKERS_")
            ) from None

    def _qualification_ids(self, rel, product_id: str) -> list[str]:
        return list(
            self.session.scalars(
                select(LeadQualification.id).where(
                    LeadQualification.organization_id == self.tenant.organization_id,
                    LeadQualification.organization_company_id == rel.id,
                    LeadQualification.product_id == product_id,
                )
            )
        )

    def _scope_contacts(self, rel, product_id: str) -> list[ContactCandidate]:
        ids = self._qualification_ids(rel, product_id)
        if not ids:
            return []
        return list(
            self.session.scalars(
                select(ContactCandidate).where(
                    ContactCandidate.organization_id == self.tenant.organization_id,
                    ContactCandidate.organization_company_id == rel.id,
                    ContactCandidate.qualification_id.in_(ids),
                )
            )
        )

    def _evidence(self, contact_id: str) -> list[ContactEvidence]:
        return list(
            self.session.scalars(
                select(ContactEvidence)
                .where(
                    ContactEvidence.organization_id == self.tenant.organization_id,
                    ContactEvidence.contact_id == contact_id,
                )
                .order_by(ContactEvidence.retrieved_at.desc(), ContactEvidence.id)
            )
        )

    def _provider(self, key: str):
        try:
            return self.registry.get(key)
        except ValueError:
            return None

    def _audit(self, action: str, entity_type: str, entity_id: str, meta: dict) -> None:
        self.session.add(
            AuditLog(
                organization_id=self.tenant.organization_id,
                actor_user_id=self.tenant.actor_user_id,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                metadata_json=meta,
            )
        )

    def last_run(self, relationship_id: str, product_id: str) -> dict[str, Any] | None:
        rows = self.session.scalars(
            select(AuditLog)
            .where(
                AuditLog.organization_id == self.tenant.organization_id,
                AuditLog.entity_type == "organization_company",
                AuditLog.entity_id == relationship_id,
                AuditLog.action == RUN_ACTION,
            )
            .order_by(AuditLog.created_at.desc())
            .limit(25)
        )
        for row in rows:
            meta = row.metadata_json or {}
            if meta.get("product_id") == product_id:
                return {**meta, "at": _aware(row.created_at)}
        return None

    # ------------------------------------------------------------------ discovery
    def discover(
        self,
        relationship_id: str,
        product_id: str | None = None,
        *,
        allow_provider_credits: bool = False,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        require_permission(self.tenant, Permission.RUN_PUBLIC_RESEARCH)
        if allow_provider_credits:
            require_permission(self.tenant, Permission.SPEND_PROVIDER_CREDITS)
        rel = self._relationship(relationship_id)
        qualification = self._latest_qualification(rel, product_id)
        product = self._product(qualification.product_id)
        specs = self.persona_specs(product)
        if not specs:
            raise ConflictError("DECISION_MAKERS_PERSONAS_REQUIRED")
        domain = self.contacts._domain(rel.company_id)
        company = self.session.get(Company, rel.company_id)
        company_name = company.canonical_name if company else domain
        report = RunReport()
        observed_ids: set[str] = set()

        public_observations = self._public_phase(
            rel, product.id, domain, company_name, force_refresh, report
        )
        for observation in public_observations:
            observed_ids.add(self._merge(rel, product.id, qualification, observation).id)

        hunter = self._provider("HUNTER")
        if allow_provider_credits and hunter is not None and hunter.configured:
            for observation in self._hunter_phase(
                rel, product.id, domain, specs, force_refresh, report
            ):
                observed_ids.add(self._merge(rel, product.id, qualification, observation).id)
        elif allow_provider_credits:
            report.notes.append("HUNTER_NOT_CONFIGURED")
        else:
            report.notes.append("PROVIDER_CREDITS_NOT_ALLOWED")
        report.notes.append("SEARCH_ENGINE_LOOKUP_NOT_CONFIGURED")

        scope = self._scope_contacts(rel, product.id)
        self._adopt_current_qualification(scope, qualification.id)
        self._resolve_roles(scope, specs, observed_ids, force_refresh, report)
        self._evaluate(scope, specs)

        if allow_provider_credits and hunter is not None and hunter.configured:
            self._email_phase(rel, domain, scope, hunter, force_refresh, report)
            self._evaluate(scope, specs)

        outcome = self._outcome(scope)
        for contact in scope:
            if contact.id in observed_ids:
                contact.last_discovery_at = self.now
        self._audit(
            RUN_ACTION,
            "organization_company",
            rel.id,
            {
                "workflow_version": WORKFLOW_VERSION,
                "product_id": product.id,
                "qualification_id": qualification.id,
                "outcome": outcome.value,
                "credits_allowed": allow_provider_credits,
                "force_refresh": force_refresh,
                "personas": [s.name for s in specs],
                "people_in_scope": len(scope),
                "operations": report.as_dict(),
            },
        )
        self.session.flush()
        summary = self.summary(rel.id, product.id)
        summary["run"] = {"outcome": outcome.value, "operations": report.as_dict()}
        return summary

    def _adopt_current_qualification(self, scope, qualification_id: str) -> None:
        """Carry people forward to the latest qualification (not those already in a draft)."""

        stale = [c for c in scope if c.qualification_id != qualification_id]
        if not stale:
            return
        drafted = set(
            self.session.scalars(
                select(CampaignDraft.contact_id).where(
                    CampaignDraft.organization_id == self.tenant.organization_id,
                    CampaignDraft.contact_id.in_([c.id for c in stale]),
                )
            )
        )
        for contact in stale:
            if contact.id not in drafted:
                contact.qualification_id = qualification_id

    def _public_phase(
        self, rel, product_id, domain, company_name, force, report
    ) -> list[Observation]:
        public = self._provider("PUBLIC_WEB")
        if public is None or self.page_scanner is None:
            report.notes.append("PUBLIC_WEB_UNAVAILABLE")
            return []
        # Cache per product: people are scoped to a product's qualification.
        usage, cached = self.contacts._usage(
            public, rel.company_id, f"LEADERSHIP_SCAN:{product_id}", self.contacts.search_ttl, force
        )
        if cached:
            report.public_cached = True
            return []
        scan = self.page_scanner.scan(domain)
        usage.completed_at, usage.credits_used = self.now, 0
        report.public_pages_fetched = scan.fetched
        people = [(page, person) for page in scan.pages for person in page.people]
        if not people and self.ai_client is not None and self.ai_client.configured:
            people = self._ai_extract(rel, company_name, scan.pages, report)
        usage.success = bool(scan.pages) or not scan.errors
        usage.response_status = (
            "SUCCESS" if people else "NO_RESULTS" if usage.success else "FAILURE"
        )
        if scan.errors and not scan.pages:
            usage.error_category = scan.errors[0]
        report.public_people_found = len(people)
        self._mark_no_longer_listed(rel, scan.pages, people)
        observations = []
        for page, person in people:
            first, last = split_name(person.name)
            evidence = [
                {
                    "source_type": EvidenceSourceType.COMPANY_WEBSITE,
                    "provider_key": "PUBLIC_WEB",
                    "source_url": page.url[:2048],
                    "claimed_name": person.name,
                    "claimed_title": person.title,
                    "claimed_company": company_name,
                    "snippet": person.snippet[:500],
                    "retrieved_at": page.retrieved_at,
                    "still_on_page": True,
                    "confidence": 85,
                }
            ]
            if person.linkedin_url:
                evidence.append(
                    {
                        "source_type": EvidenceSourceType.PUBLIC_PROFILE_URL,
                        "provider_key": "PUBLIC_WEB",
                        "source_url": person.linkedin_url,
                        "claimed_name": person.name,
                        "snippet": f"Profile link published on {page.url[:300]} (not fetched)",
                        "retrieved_at": page.retrieved_at,
                        "confidence": 60,
                    }
                )
            observations.append(
                Observation(
                    source=EvidenceSourceType.COMPANY_WEBSITE,
                    provider_key="PUBLIC_WEB",
                    full_name=person.name,
                    first_name=first,
                    last_name=last,
                    title=person.title,
                    linkedin=person.linkedin_url,
                    evidence=evidence,
                )
            )
        return observations

    def _ai_extract(self, rel, company_name, pages, report):
        """One AI call over the most promising page; every result must appear verbatim."""

        from marketingiq.infrastructure.leadership_pages import PagePerson

        candidates = [p for p in pages if len(p.text) > 200]
        if not candidates:
            return []
        page = max(candidates, key=lambda p: len(p.text))
        usage, _ = self.contacts._usage(
            _Operator("OPENAI"), rel.company_id, "PEOPLE_EXTRACTION", timedelta(0), True
        )
        report.ai_calls += 1
        try:
            raw = self.ai_client.extract_people(company_name, page.url, page.text)
        except Exception as error:  # noqa: BLE001 - AI failure must never break discovery
            code = getattr(error, "code", None) or type(error).__name__
            usage.response_status, usage.error_category = "FAILURE", code
            usage.completed_at = self.now
            report.ai_errors.append(code)
            return []
        usage.success, usage.completed_at = True, self.now
        haystack = re.sub(r"\s+", " ", page.text).lower()
        people = []
        seen: set[str] = set()
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict):
                continue
            name = re.sub(r"\s+", " ", str(item.get("name") or "")).strip()
            title = re.sub(r"\s+", " ", str(item.get("title") or "")).strip()
            if not (2 <= len(name) <= 80 and 2 <= len(title) <= 160):
                continue
            # Anti-fabrication: both values must be copied from the page text.
            if not _verbatim(name, haystack) or not _verbatim(title, haystack):
                continue
            if len(name.split()) < 2 or name.lower() in seen:
                continue
            seen.add(name.lower())
            people.append((page, PagePerson(name, title[:255], f"{name} — {title}"[:500], None)))
        usage.response_status = "SUCCESS" if people else "NO_RESULTS"
        return people[:40]

    def _mark_no_longer_listed(self, rel, pages, people) -> None:
        """A person we saw on a page that was re-fetched but no longer lists them."""

        fetched = {p.url.rstrip("/").lower() for p in pages}
        present = {(page.url.rstrip("/").lower(), name_key(person.name)) for page, person in people}
        if not fetched:
            return
        rows = self.session.scalars(
            select(ContactEvidence)
            .join(ContactCandidate, ContactCandidate.id == ContactEvidence.contact_id)
            .where(
                ContactEvidence.organization_id == self.tenant.organization_id,
                ContactCandidate.organization_company_id == rel.id,
                ContactEvidence.source_type == EvidenceSourceType.COMPANY_WEBSITE,
            )
        )
        for row in rows:
            url = (row.source_url or "").rstrip("/").lower()
            if url in fetched and (url, name_key(row.claimed_name)) not in present:
                row.still_on_page = False
                row.last_seen_on = row.last_seen_on or row.retrieved_at

    def _hunter_phase(self, rel, product_id, domain, specs, force, report) -> list[Observation]:
        hunter = self._provider("HUNTER")
        usage, cached = self.contacts._usage(
            hunter,
            rel.company_id,
            f"DECISION_MAKER_SEARCH:{product_id}",
            self.contacts.search_ttl,
            force,
        )
        if cached:
            report.hunter_cached = True
            return []
        departments = sorted({d for s in specs for d in s.hunter_departments})
        seniorities = sorted({x for s in specs for x in s.hunter_seniorities})
        report.hunter_searches += 1
        try:
            if hasattr(hunter, "search_decision_makers"):
                result = hunter.search_decision_makers(
                    domain,
                    departments=departments,
                    seniorities=seniorities,
                    max_results=HUNTER_SEARCH_LIMIT,
                )
            else:
                result = hunter.search_contacts(domain, HUNTER_SEARCH_LIMIT)
        except ProviderError as error:
            usage.response_status, usage.error_category = "FAILURE", error.category
            usage.completed_at = self.now
            report.notes.append(f"HUNTER_{error.category}")
            return []
        usage.success = True
        usage.response_status = "SUCCESS" if result.contacts else "NO_RESULTS"
        usage.request_identifier = result.request_identifier
        usage.credits_used, usage.credits_remaining = result.credits_used, result.credits_remaining
        usage.completed_at = self.now
        report.credits_used += int(result.credits_used or 0)
        observations = []
        for found in result.contacts:
            name = found.full_name or " ".join(x for x in (found.first_name, found.last_name) if x)
            if not name:
                continue  # a role mailbox without a named person is not a decision-maker
            report.hunter_people_found += 1
            observations.append(self._observation_from_provider(found, hunter.key, name))
        return observations

    def _observation_from_provider(self, found, provider_key: str, name: str) -> Observation:
        evidence = [
            {
                "source_type": EvidenceSourceType.PROVIDER_RECORD,
                "provider_key": provider_key,
                "provider_reference": found.reference,
                "claimed_name": name,
                "claimed_title": found.position_raw or found.job_title,
                "snippet": "Provider record"
                + (f" (department: {found.department})" if found.department else "")
                + (f", seniority: {found.seniority}" if found.seniority else ""),
                "retrieved_at": self.now,
                "confidence": found.confidence,
            }
        ]
        for source in found.sources[:5]:
            if not _http_url(source.uri):
                continue
            evidence.append(
                {
                    "source_type": EvidenceSourceType.PROVIDER_SOURCE_PAGE,
                    "provider_key": provider_key,
                    "source_url": source.uri[:2048],
                    "claimed_name": name,
                    "snippet": "Page where the provider observed this person"
                    + (f" (first seen {source.extracted_on})" if source.extracted_on else ""),
                    "retrieved_at": self.now,
                    "last_seen_on": parse_provider_date(source.last_seen_on),
                    "still_on_page": source.still_on_page,
                }
            )
        linkedin = normalize_linkedin(found.linkedin)
        if linkedin:
            evidence.append(
                {
                    "source_type": EvidenceSourceType.PUBLIC_PROFILE_URL,
                    "provider_key": provider_key,
                    "source_url": linkedin,
                    "claimed_name": name,
                    "snippet": "Profile link supplied by the provider (not fetched)",
                    "retrieved_at": self.now,
                }
            )
        return Observation(
            source=EvidenceSourceType.PROVIDER_RECORD,
            provider_key=provider_key,
            full_name=name,
            first_name=found.first_name,
            last_name=found.last_name,
            reference=found.reference,
            title=found.job_title or found.position_raw,
            position_raw=found.position_raw,
            department=found.department,
            seniority=found.seniority,
            email=found.email,
            email_confidence=found.confidence,
            email_verification=found.verification_status,
            email_verification_date=found.verification_date,
            linkedin=linkedin,
            decision_maker=found.decision_maker,
            evidence=evidence,
        )

    # ------------------------------------------------------------------ merge
    def _find_existing(self, rel, product_id, observation) -> ContactCandidate | None:
        scope = self._scope_contacts(rel, product_id)
        email = (observation.email or "").strip().lower()
        if email:
            for contact in scope:
                if any(c.email == email for c in contact.channels):
                    return contact
        if observation.reference:
            for contact in scope:
                if (
                    contact.provider_key == observation.provider_key
                    and contact.provider_contact_reference == observation.reference
                ):
                    return contact
        linkedin = normalize_linkedin(observation.linkedin)
        if linkedin:
            for contact in scope:
                if contact.linkedin_url == linkedin:
                    return contact
        key = name_key(observation.full_name)
        if key:
            for contact in scope:
                if name_key(contact.full_name) == key:
                    return contact
        return None

    def _merge(self, rel, product_id, qualification, observation: Observation) -> ContactCandidate:
        contact = self._find_existing(rel, product_id, observation)
        third_party = observation.provider_key != "PUBLIC_WEB"
        if contact is None:
            reference = observation.reference
            if reference and self.session.scalar(
                select(ContactCandidate.id).where(
                    ContactCandidate.organization_id == self.tenant.organization_id,
                    ContactCandidate.provider_key == observation.provider_key,
                    ContactCandidate.provider_contact_reference == reference,
                )
            ):
                reference = None
            contact = ContactCandidate(
                organization_id=self.tenant.organization_id,
                organization_company_id=rel.id,
                company_id=rel.company_id,
                qualification_id=qualification.id,
                provider_key=observation.provider_key,
                provider_contact_reference=reference,
                normalized_buyer_role=qualification.recommended_buyer_role.strip().lower()[:200],
                buyer_role_match=legacy_role_match(RoleMatch.NONE, 0, None),
                buyer_role_match_reason="Pending decision-maker evaluation",
                discovered_at=self.now,
                classification=DataClassification.THIRD_PARTY_LICENSED
                if third_party
                else DataClassification.PUBLIC_EVIDENCE,
                redistribution_status=RedistributionStatus.RESTRICTED
                if third_party
                else RedistributionStatus.UNKNOWN,
            )
            self.session.add(contact)
        elif third_party and contact.classification == DataClassification.PUBLIC_EVIDENCE:
            contact.classification = DataClassification.THIRD_PARTY_LICENSED
            contact.redistribution_status = RedistributionStatus.RESTRICTED

        contact.full_name = contact.full_name or (observation.full_name or "")[:220] or None
        contact.first_name = contact.first_name or (observation.first_name or "")[:100] or None
        contact.last_name = contact.last_name or (observation.last_name or "")[:100] or None
        # The company's own website is the authority for the current title.
        if observation.title and (
            observation.source == EvidenceSourceType.COMPANY_WEBSITE or not contact.job_title
        ):
            contact.job_title = observation.title[:200]
        if observation.position_raw:
            contact.position_raw = observation.position_raw[:255]
        contact.department = contact.department or (observation.department or "")[:100] or None
        contact.seniority = contact.seniority or (observation.seniority or "")[:100] or None
        if observation.decision_maker is not None:
            contact.decision_maker = observation.decision_maker
        linkedin = normalize_linkedin(observation.linkedin)
        if linkedin and not contact.linkedin_url:
            contact.linkedin_url = linkedin
        if observation.email_confidence is not None and contact.confidence is None:
            contact.confidence = observation.email_confidence
        self.session.flush()

        existing = self._evidence(contact.id)
        for item in observation.evidence:
            key = (
                str(item["source_type"]),
                item.get("source_url") or item.get("provider_reference") or "",
                item.get("claimed_title") or "",
            )
            match = next(
                (
                    e
                    for e in existing
                    if (
                        e.source_type,
                        e.source_url or e.provider_reference or "",
                        e.claimed_title or "",
                    )
                    == key
                ),
                None,
            )
            if match:
                match.retrieved_at = item.get("retrieved_at") or self.now
                if item.get("still_on_page") is not None:
                    match.still_on_page = item["still_on_page"]
                if item.get("last_seen_on"):
                    match.last_seen_on = item["last_seen_on"]
                continue
            row = evidence_row(self.tenant.organization_id, contact.id, item)
            self.session.add(row)
            existing.append(row)
        if observation.email:
            channel = self.contacts._add_email(contact, observation.email, observation.provider_key)
            channel.origin = channel.origin or EmailOrigin.PROVIDER_FOUND
            self._apply_reported_verification(
                channel, observation.email_verification, observation.email_verification_date
            )
        self.session.flush()
        self.session.expire(contact, ["channels"])
        return contact

    def _apply_reported_verification(self, channel, status, date) -> None:
        """Accept a provider-reported verification only if it is recent enough."""

        verified = parse_provider_date(date)
        mapped = VERIFY_MAP.get((status or "").lower())
        if not mapped or verified is None:
            return
        if verified < self.now - self.contacts.verify_ttl:
            return
        if channel.verified_at and _aware(channel.verified_at) >= verified:
            return
        channel.verification_status = mapped
        channel.verified_at = verified
        channel.verification_source = "PROVIDER_REPORTED"

    # ------------------------------------------------------------------ evaluation
    def _resolve_roles(self, scope, specs, observed_ids, force, report) -> None:
        targets = [
            c
            for c in scope
            if c.role_resolver != "MANUAL"
            and (force or c.role_match is None or c.id in observed_ids)
        ]
        if not targets:
            return
        resolver = RoleResolver(self.ai_client)
        candidates = [
            PersonCandidate(c.id, c.job_title or c.position_raw, c.department, c.seniority)
            for c in targets
        ]
        results = resolver.resolve_many(candidates, specs)
        if resolver.report.ai_attempted:
            report.ai_calls += 1
            usage, _ = self.contacts._usage(
                _Operator("OPENAI"),
                targets[0].company_id,
                "ROLE_RESOLUTION",
                timedelta(0),
                True,
            )
            usage.success = resolver.report.ai_succeeded
            usage.response_status = "SUCCESS" if usage.success else "FAILURE"
            usage.error_category = resolver.report.ai_error
            usage.completed_at = self.now
            if resolver.report.ai_error:
                report.ai_errors.append(resolver.report.ai_error)
        for contact in targets:
            result = results[contact.id]
            contact.persona_id = result.persona.id if result.persona else None
            contact.persona_name = result.persona.name if result.persona else None
            contact.role_match = result.role_match.value
            contact.role_confidence = result.confidence
            contact.role_reason = result.reason[:300]
            contact.role_resolver = result.resolver
            if result.persona:
                contact.normalized_buyer_role = result.persona.name.lower()[:200]

    def _evaluate(self, scope, specs) -> None:
        for contact in scope:
            if contact.role_match is None:
                continue
            evidence = [
                EvidenceView(
                    e.source_type,
                    e.source_url,
                    e.claimed_title,
                    _aware(e.retrieved_at),
                    _aware(e.last_seen_on),
                    e.still_on_page,
                )
                for e in self._evidence(contact.id)
            ]
            emails = [
                EmailView(
                    c.email,
                    EmailVerificationStatus(c.verification_status),
                    _aware(c.verified_at),
                    c.origin,
                )
                for c in contact.channels
            ]
            role = RoleMatch(contact.role_match)
            employment, employment_reason = employment_status(evidence, emails, self.now)
            email = best_email(emails)
            grade, components = suitability(role, employment, evidence, email)
            components["employment_reason"] = employment_reason
            previous = contact.suitability_components or {}
            if previous.get("email_find_attempted_at"):
                components["email_find_attempted_at"] = previous["email_find_attempted_at"]
            contact.employment_status = employment.value
            contact.employment_checked_at = self.now
            contact.suitability = grade.value
            contact.suitability_components = components
            contact.contact_status = contact_status(
                role, employment, grade, email, contact.review_status
            ).value
            contact.buyer_role_match = legacy_role_match(
                role, contact.role_confidence or 0, contact.review_status
            )
            contact.buyer_role_match_reason = (contact.role_reason or "Role evaluated")[:255]
        self._rank(scope, specs)

    def _rank(self, scope, specs) -> None:
        limits = {s.name: s.max_contacts for s in specs}
        groups: dict[str, list[ContactCandidate]] = {}
        for contact in scope:
            contact.persona_rank = None
            if contact.persona_name and ContactStatus(contact.contact_status or "EXCLUDED") in (
                RANKABLE
            ):
                groups.setdefault(contact.persona_name, []).append(contact)
        for name, members in groups.items():
            members.sort(
                key=lambda c: (
                    0 if c.review_status == ReviewStatus.APPROVED else 1,
                    SUITABILITY_ORDER[Suitability(c.suitability)],
                    EMPLOYMENT_ORDER.get(c.employment_status, 9),
                    -(c.role_confidence or 0),
                    STATUS_ORDER[ContactStatus(c.contact_status)],
                    -int((c.suitability_components or {}).get("score", 0)),
                    (c.full_name or "").lower(),
                )
            )
            for index, contact in enumerate(members[: limits.get(name, 2)]):
                contact.persona_rank = index + 1

    def _email_phase(self, rel, domain, scope, hunter, force, report) -> None:
        budget = MAX_EMAIL_OPERATIONS
        ranked = sorted(
            [c for c in scope if c.persona_rank is not None],
            key=lambda c: (c.persona_rank, c.persona_name or ""),
        )
        for contact in ranked:
            if budget <= 0:
                report.notes.append("EMAIL_OPERATION_CAP_REACHED")
                break
            if not contact.channels:
                if self._find_email(rel, domain, contact, hunter, force, report):
                    budget -= 1
            if budget <= 0:
                continue
            channel = self._channel_to_verify(contact, force)
            if channel is not None:
                self._verify(contact, channel, hunter, report)
                budget -= 1

    def _channel_to_verify(self, contact, force) -> ContactEmail | None:
        for channel in contact.channels:
            status = EmailVerificationStatus(channel.verification_status)
            if status == EmailVerificationStatus.VALID and not force:
                return None
        candidates = [
            c
            for c in contact.channels
            if force
            or c.verified_at is None
            or _aware(c.verified_at) < self.now - self.contacts.verify_ttl
        ]
        return candidates[0] if candidates else None

    def _find_email(self, rel, domain, contact, hunter, force, report) -> bool:
        components = dict(contact.suitability_components or {})
        attempted = components.get("email_find_attempted_at")
        if attempted and not force:
            when = parse_provider_date(attempted)
            if when and when >= self.now - self.contacts.find_ttl:
                return False
        if not contact.first_name or not contact.last_name:
            return False
        usage, _ = self.contacts._usage(
            hunter, rel.company_id, "EMAIL_FIND", self.contacts.find_ttl, True
        )
        report.email_finds += 1
        try:
            result = hunter.find_email(
                {"domain": domain, "first_name": contact.first_name, "last_name": contact.last_name}
            )
        except ProviderError as error:
            usage.response_status, usage.error_category = "FAILURE", error.category
            usage.completed_at = self.now
            report.notes.append(f"EMAIL_FIND_{error.category}")
            return True
        usage.success, usage.completed_at = True, self.now
        usage.response_status = "SUCCESS" if result.contacts else "NO_RESULTS"
        usage.credits_used, usage.credits_remaining = result.credits_used, result.credits_remaining
        usage.request_identifier = result.request_identifier
        report.credits_used += int(result.credits_used or 0)
        # Only a completed lookup suppresses automatic retries (provider errors can be retried).
        components["email_find_attempted_at"] = self.now.isoformat()
        contact.suitability_components = components
        if result.contacts and result.contacts[0].email:
            found = result.contacts[0]
            name = contact.full_name or f"{contact.first_name} {contact.last_name}"
            observation = self._observation_from_provider(found, hunter.key, name)
            for item in observation.evidence:
                self.session.add(evidence_row(self.tenant.organization_id, contact.id, item))
            channel = self.contacts._add_email(contact, found.email, hunter.key)
            channel.origin = EmailOrigin.PROVIDER_FOUND
            self._apply_reported_verification(
                channel, found.verification_status, found.verification_date
            )
            self.session.flush()
            self.session.expire(contact, ["channels"])
        return True

    def _verify(self, contact, channel, hunter, report) -> None:
        usage, _ = self.contacts._usage(
            hunter, contact.company_id, "EMAIL_VERIFY", self.contacts.verify_ttl, True
        )
        report.email_verifications += 1
        try:
            result = hunter.verify_email(channel.email)
        except ProviderError as error:
            usage.response_status, usage.error_category = "FAILURE", error.category
            usage.completed_at = self.now
            report.notes.append(f"EMAIL_VERIFY_{error.category}")
            return
        found = result.contacts[0] if result.contacts else None
        raw = (found.reference or "unknown").lower() if found else "unknown"
        channel.verification_status = VERIFY_MAP.get(raw, EmailVerificationStatus.UNVERIFIABLE)
        channel.verification_score = found.confidence if found else None
        channel.verified_at = self.now
        channel.verification_source = "HUNTER_VERIFIER"
        contact.last_verified_at = self.now
        usage.success, usage.response_status, usage.completed_at = True, "SUCCESS", self.now
        usage.credits_used, usage.credits_remaining = result.credits_used, result.credits_remaining
        report.credits_used += int(result.credits_used or 0)
        self.session.add(
            ContactEvidence(
                organization_id=self.tenant.organization_id,
                contact_id=contact.id,
                source_type=EvidenceSourceType.EMAIL_VERIFICATION.value,
                provider_key=hunter.key,
                snippet=f"Mailbox verification result: {channel.verification_status}",
                retrieved_at=self.now,
            )
        )
        self.session.flush()

    @staticmethod
    def _outcome(scope) -> DiscoveryOutcome:
        evaluated = [c for c in scope if c.role_match is not None]
        if not evaluated:
            return DiscoveryOutcome.NO_CONTACTS_FOUND
        relevant = [
            c
            for c in evaluated
            if c.review_status != ReviewStatus.EXCLUDED
            and (
                c.role_match in {RoleMatch.HIGH, RoleMatch.MEDIUM}
                or c.review_status == ReviewStatus.APPROVED
            )
        ]
        if not relevant:
            return DiscoveryOutcome.NO_RELEVANT_DECISION_MAKER
        ranked = [c for c in relevant if c.persona_rank is not None]
        if not ranked:
            return DiscoveryOutcome.INSUFFICIENT_EVIDENCE
        statuses = {c.contact_status for c in ranked}
        if ContactStatus.READY_FOR_DRAFT in statuses:
            return DiscoveryOutcome.READY_FOR_DRAFT
        if ContactStatus.EMAIL_UNVERIFIED in statuses:
            return DiscoveryOutcome.EMAIL_UNVERIFIED
        return DiscoveryOutcome.CONTACT_FOUND_EMAIL_MISSING

    # ------------------------------------------------------------------ read models
    def _resolve_product_id(self, rel, product_id: str | None) -> str | None:
        if product_id:
            self._product(product_id)
            return product_id
        latest = self.session.scalar(
            select(LeadQualification.product_id)
            .where(
                LeadQualification.organization_id == self.tenant.organization_id,
                LeadQualification.organization_company_id == rel.id,
            )
            .order_by(LeadQualification.qualified_at.desc())
        )
        return latest

    def ready_count(self, relationship_id: str, product_id: str, qualification_id: str) -> int:
        rel = self._relationship(relationship_id)
        return sum(
            1
            for c in self._scope_contacts(rel, product_id)
            if c.qualification_id == qualification_id
            and c.contact_status == ContactStatus.READY_FOR_DRAFT
        )

    def summary(self, relationship_id: str, product_id: str | None = None) -> dict[str, Any]:
        require_permission(self.tenant, Permission.READ)
        rel = self._relationship(relationship_id)
        product_id = self._resolve_product_id(rel, product_id)
        if product_id is None:
            return {
                "relationship_id": rel.id,
                "product_id": None,
                "outcome": None,
                "personas": [],
                "contacts": [],
                "last_run": None,
                "providers": self._provider_status(),
            }
        product = self._product(product_id)
        specs = self.persona_specs(product)
        scope = self._scope_contacts(rel, product.id)
        evaluated = [c for c in scope if c.role_match is not None]
        rows = [self._row(c) for c in scope]
        rows.sort(
            key=lambda r: (
                r["persona_rank"] is None,
                r["persona_name"] or "~",
                r["persona_rank"] or 99,
                STATUS_ORDER.get(ContactStatus(r["contact_status"]), 9)
                if r["contact_status"]
                else 9,
                (r["full_name"] or "").lower(),
            )
        )
        last = self.last_run(rel.id, product.id)
        return {
            "relationship_id": rel.id,
            "product_id": product.id,
            "product_name": product.name,
            "outcome": self._outcome(scope).value if evaluated else None,
            "personas": [
                {
                    **spec_output(s),
                    "selected": [
                        r["id"] for r in rows if r["persona_name"] == s.name and r["persona_rank"]
                    ],
                }
                for s in specs
            ],
            "derived_personas": not self.has_configured_personas(product.id),
            "contacts": rows,
            "last_run": {
                "at": last["at"],
                "outcome": last.get("outcome"),
                "credits_allowed": last.get("credits_allowed"),
                "operations": last.get("operations"),
            }
            if last
            else None,
            "providers": self._provider_status(),
        }

    def _provider_status(self) -> dict[str, Any]:
        hunter = self._provider("HUNTER")
        return {
            "hunter_configured": bool(hunter and hunter.configured),
            "ai_configured": bool(self.ai_client and self.ai_client.configured),
            "ai_model": getattr(self.ai_client, "model", None) if self.ai_client else None,
        }

    def _row(self, contact: ContactCandidate) -> dict[str, Any]:
        emails = [
            EmailView(
                c.email, EmailVerificationStatus(c.verification_status), c.verified_at, c.origin
            )
            for c in contact.channels
        ]
        email = best_email(emails)
        components = contact.suitability_components or {}
        return {
            "id": contact.id,
            "persona_id": contact.persona_id,
            "persona_name": contact.persona_name,
            "persona_rank": contact.persona_rank,
            "full_name": contact.full_name,
            "first_name": contact.first_name,
            "last_name": contact.last_name,
            "job_title": contact.job_title,
            "department": contact.department,
            "seniority": contact.seniority,
            "linkedin_url": contact.linkedin_url,
            "email": email.email if email else None,
            "email_state": email_state(email),
            "email_verification_status": email.verification_status if email else None,
            "employment_status": contact.employment_status,
            "employment_reason": components.get("employment_reason"),
            "role_match": contact.role_match,
            "role_confidence": contact.role_confidence,
            "role_reason": contact.role_reason,
            "role_resolver": contact.role_resolver,
            "suitability": contact.suitability,
            "suitability_score": components.get("score"),
            "suitability_components": {
                k: components.get(k)
                for k in ("role", "employment", "evidence", "email", "score", "rule")
                if k in components
            },
            "contact_status": contact.contact_status,
            "review_status": contact.review_status,
            "review_note": contact.review_note,
            "reviewed_at": contact.reviewed_at,
            "provider_key": contact.provider_key,
            "classification": contact.classification,
            "qualification_id": contact.qualification_id,
            "discovered_at": contact.discovered_at,
            "last_discovery_at": contact.last_discovery_at,
            "employment_checked_at": contact.employment_checked_at,
        }

    def _scoped_contact(self, relationship_id: str, contact_id: str) -> ContactCandidate:
        return self.contacts.get(relationship_id, contact_id)

    def detail(self, relationship_id: str, contact_id: str) -> dict[str, Any]:
        require_permission(self.tenant, Permission.READ)
        contact = self._scoped_contact(relationship_id, contact_id)
        history = self.session.scalars(
            select(AuditLog)
            .where(
                AuditLog.organization_id == self.tenant.organization_id,
                AuditLog.entity_id == contact.id,
            )
            .order_by(AuditLog.created_at.desc())
            .limit(50)
        )
        return {
            **self._row(contact),
            "emails": [
                {
                    "id": c.id,
                    "email": c.email,
                    "origin": c.origin,
                    "source_provider": c.source_provider,
                    "verification_status": c.verification_status,
                    "verification_source": c.verification_source,
                    "verification_score": c.verification_score,
                    "found_at": c.found_at,
                    "verified_at": c.verified_at,
                    "state": email_state(
                        EmailView(
                            c.email,
                            EmailVerificationStatus(c.verification_status),
                            c.verified_at,
                            c.origin,
                        )
                    ),
                }
                for c in contact.channels
            ],
            "evidence": [
                {
                    "id": e.id,
                    "source_type": e.source_type,
                    "provider_key": e.provider_key,
                    "source_url": e.source_url,
                    "claimed_name": e.claimed_name,
                    "claimed_title": e.claimed_title,
                    "claimed_company": e.claimed_company,
                    "snippet": e.snippet,
                    "retrieved_at": e.retrieved_at,
                    "last_seen_on": e.last_seen_on,
                    "still_on_page": e.still_on_page,
                    "confidence": e.confidence,
                }
                for e in self._evidence(contact.id)
            ],
            "position_raw": contact.position_raw,
            "decision_maker_flag": contact.decision_maker,
            "history": [
                {
                    "action": h.action,
                    "at": h.created_at,
                    "actor_user_id": h.actor_user_id,
                    "details": h.metadata_json,
                }
                for h in history
            ],
        }

    # ------------------------------------------------------------------ manual controls
    def review(
        self, relationship_id: str, contact_id: str, action: str, note: str | None = None
    ) -> dict[str, Any]:
        require_permission(self.tenant, Permission.RUN_QUALIFICATION)
        contact = self._scoped_contact(relationship_id, contact_id)
        action = action.upper()
        if action not in {"APPROVE", "EXCLUDE", "RESET"}:
            raise ConflictError("DECISION_MAKER_REVIEW_ACTION_INVALID")
        if action == "APPROVE" and not contact.full_name:
            raise ConflictError("DECISION_MAKER_APPROVAL_REQUIRES_NAMED_PERSON")
        before = {"review_status": contact.review_status, "contact_status": contact.contact_status}
        contact.review_status = {
            "APPROVE": ReviewStatus.APPROVED.value,
            "EXCLUDE": ReviewStatus.EXCLUDED.value,
            "RESET": None,
        }[action]
        contact.review_note = (note or "").strip()[:500] or None
        contact.reviewed_by_user_id = self.tenant.actor_user_id or None
        contact.reviewed_at = self.now
        self._reevaluate(contact)
        self._audit(
            "decision_makers.review",
            "contact_candidate",
            contact.id,
            {
                "action": action,
                "note": contact.review_note,
                "before": before,
                "after": {
                    "review_status": contact.review_status,
                    "contact_status": contact.contact_status,
                },
                "employment_status": contact.employment_status,
                "role_match": contact.role_match,
            },
        )
        self.session.flush()
        return self.detail(relationship_id, contact.id)

    def _reevaluate(self, contact: ContactCandidate) -> None:
        qualification = self.session.get(LeadQualification, contact.qualification_id)
        rel = self._relationship(contact.organization_company_id)
        product = self._product(qualification.product_id)
        specs = self.persona_specs(product)
        scope = self._scope_contacts(rel, product.id)
        if contact.role_match is None:
            self._resolve_roles(scope, specs, {contact.id}, False, RunReport())
        self._evaluate(scope, specs)

    def find_email(self, relationship_id: str, contact_id: str) -> dict[str, Any]:
        require_permission(self.tenant, Permission.SPEND_PROVIDER_CREDITS)
        contact = self._scoped_contact(relationship_id, contact_id)
        hunter = self._provider("HUNTER")
        if hunter is None or not hunter.configured:
            raise ConflictError("HUNTER_NOT_CONFIGURED")
        if not contact.first_name or not contact.last_name:
            raise ConflictError("EMAIL_FIND_NAME_REQUIRED")
        rel = self._relationship(relationship_id)
        report = RunReport()
        self._find_email(rel, self.contacts._domain(rel.company_id), contact, hunter, True, report)
        self._reevaluate(contact)
        self._audit(
            "decision_makers.email_find",
            "contact_candidate",
            contact.id,
            {"provider": hunter.key, "operations": report.as_dict()},
        )
        self.session.flush()
        return self.detail(relationship_id, contact.id)

    def verify_email(self, relationship_id: str, contact_id: str) -> dict[str, Any]:
        require_permission(self.tenant, Permission.SPEND_PROVIDER_CREDITS)
        contact = self._scoped_contact(relationship_id, contact_id)
        hunter = self._provider("HUNTER")
        if hunter is None or not hunter.configured:
            raise ConflictError("HUNTER_NOT_CONFIGURED")
        if not contact.channels:
            raise ConflictError("EMAIL_REQUIRED")
        report = RunReport()
        channel = next(
            (
                c
                for c in contact.channels
                if EmailVerificationStatus(c.verification_status) != EmailVerificationStatus.VALID
            ),
            contact.channels[0],
        )
        self._verify(contact, channel, hunter, report)
        self._reevaluate(contact)
        self._audit(
            "decision_makers.email_verify",
            "contact_candidate",
            contact.id,
            {"provider": hunter.key, "result": channel.verification_status},
        )
        self.session.flush()
        return self.detail(relationship_id, contact.id)


def persona_output(row: ProductPersona) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "titles": list(row.titles or []),
        "departments": list(row.departments or []),
        "seniorities": list(row.seniorities or []),
        "max_contacts": row.max_contacts,
        "priority": row.priority,
        "is_active": row.is_active,
    }


def spec_output(spec: PersonaSpec) -> dict[str, Any]:
    return {
        "id": spec.id,
        "name": spec.name,
        "titles": list(spec.titles),
        "departments": list(spec.departments),
        "seniorities": list(spec.seniorities),
        "max_contacts": spec.max_contacts,
        "priority": spec.priority,
        "hunter_departments": spec.hunter_departments,
        "hunter_seniorities": spec.hunter_seniorities,
        "normalized_titles": [normalize_text(t) for t in spec.titles],
    }


def targeting_block_reason(contact: ContactCandidate) -> str | None:
    """Why campaign drafting / sending must refuse this contact, or None if allowed."""

    if contact.review_status == ReviewStatus.EXCLUDED:
        return "CONTACT_EXCLUDED"
    if contact.review_status == ReviewStatus.APPROVED:
        return None
    if contact.employment_status in {EmploymentStatus.FORMER, EmploymentStatus.CONFLICTING}:
        return "CONTACT_EMPLOYMENT_NOT_CURRENT"
    return None
