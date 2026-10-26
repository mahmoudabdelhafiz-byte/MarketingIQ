"""Evidence → employment state, suitability and contact status.

Every rule here is deterministic and returns a short human-readable reason so the UI can show
exactly why a person was (or was not) selected. Nothing in this module calls a provider.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from marketingiq.application.role_resolver import FORMER_MARKERS, normalize_text
from marketingiq.domain.decision_makers import (
    ContactStatus,
    EmploymentStatus,
    EvidenceSourceType,
    ReviewStatus,
    RoleMatch,
    Suitability,
)
from marketingiq.domain.models import BuyerRoleMatch, EmailVerificationStatus

PROVIDER_RECENCY = timedelta(days=365)
WEBSITE_RECENCY = timedelta(days=180)
TARGETABLE_EMPLOYMENT = {
    EmploymentStatus.CURRENT_CONFIRMED,
    EmploymentStatus.CURRENT_LIKELY,
    EmploymentStatus.CURRENT_UNCERTAIN,
}
BLOCKED_EMPLOYMENT = {EmploymentStatus.FORMER, EmploymentStatus.CONFLICTING}
UNVERIFIED_EMAIL = {
    EmailVerificationStatus.UNKNOWN,
    EmailVerificationStatus.ACCEPT_ALL,
    EmailVerificationStatus.RISKY,
    EmailVerificationStatus.UNVERIFIABLE,
}
ROLE_POINTS = {RoleMatch.HIGH: 40, RoleMatch.MEDIUM: 25, RoleMatch.LOW: 10, RoleMatch.NONE: 0}
EMPLOYMENT_POINTS = {
    EmploymentStatus.CURRENT_CONFIRMED: 30,
    EmploymentStatus.CURRENT_LIKELY: 20,
    EmploymentStatus.CURRENT_UNCERTAIN: 8,
    EmploymentStatus.FORMER: 0,
    EmploymentStatus.CONFLICTING: 0,
}
_TITLE_NOISE = frozenset({"of", "and", "the", "group", "senior", "global", "regional"})


@dataclass(frozen=True)
class EvidenceView:
    source_type: str
    source_url: str | None = None
    claimed_title: str | None = None
    retrieved_at: datetime | None = None
    last_seen_on: datetime | None = None
    still_on_page: bool | None = None


@dataclass(frozen=True)
class EmailView:
    email: str
    verification_status: EmailVerificationStatus
    verified_at: datetime | None = None
    origin: str | None = None


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def parse_provider_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return _aware(parsed)


def titles_conflict(first: str | None, second: str | None) -> bool:
    """True when two titles describe clearly different roles (not just different wording)."""

    a = set(normalize_text(first).split()) - _TITLE_NOISE
    b = set(normalize_text(second).split()) - _TITLE_NOISE
    if not a or not b or a <= b or b <= a:
        return False
    return len(a & b) / len(a | b) < 0.34


def best_email(emails: Sequence[EmailView]) -> EmailView | None:
    order = {
        EmailVerificationStatus.VALID: 0,
        EmailVerificationStatus.ACCEPT_ALL: 1,
        EmailVerificationStatus.UNKNOWN: 2,
        EmailVerificationStatus.RISKY: 3,
        EmailVerificationStatus.UNVERIFIABLE: 4,
        EmailVerificationStatus.INVALID: 5,
    }
    if not emails:
        return None
    return sorted(emails, key=lambda e: (order[e.verification_status], e.email))[0]


def email_state(email: EmailView | None) -> str:
    """Display state: VERIFIED, INVALID, UNVERIFIED, PROVIDER_FOUND, PATTERN_PREDICTED, UNKNOWN."""

    if email is None:
        return "UNKNOWN"
    if email.verification_status == EmailVerificationStatus.VALID:
        return "VERIFIED"
    if email.verification_status == EmailVerificationStatus.INVALID:
        return "INVALID"
    if email.verified_at is not None:
        return "UNVERIFIED"
    if email.origin == "PATTERN_PREDICTED":
        return "PATTERN_PREDICTED"
    return "PROVIDER_FOUND" if email.origin != "CUSTOMER_PROVIDED" else "UNVERIFIED"


def employment_status(
    evidence: Sequence[EvidenceView], emails: Sequence[EmailView], now: datetime
) -> tuple[EmploymentStatus, str]:
    now = _aware(now) or datetime.now(UTC)
    titles = [e.claimed_title for e in evidence if e.claimed_title]
    if any(FORMER_MARKERS.search(t) for t in titles):
        return EmploymentStatus.FORMER, "A source describes this as a former role"

    website = [e for e in evidence if e.source_type == EvidenceSourceType.COMPANY_WEBSITE]
    official = [e for e in website if e.still_on_page is not False]
    delisted = bool(website) and not official
    provider = [
        e
        for e in evidence
        if e.source_type
        in {EvidenceSourceType.PROVIDER_RECORD, EvidenceSourceType.PROVIDER_SOURCE_PAGE}
    ]
    official_titles = [e.claimed_title for e in official if e.claimed_title]
    provider_titles = [e.claimed_title for e in provider if e.claimed_title]
    if any(titles_conflict(o, p) for o in official_titles for p in provider_titles):
        return (
            EmploymentStatus.CONFLICTING,
            "The company website and the data provider give different roles",
        )

    if any(e.retrieved_at and _aware(e.retrieved_at) >= now - WEBSITE_RECENCY for e in official):
        return (
            EmploymentStatus.CURRENT_CONFIRMED,
            "Listed with this title on the company's own website",
        )

    still_listed = [e for e in provider if e.still_on_page is True]
    recent = [
        e for e in provider if e.last_seen_on and _aware(e.last_seen_on) >= now - PROVIDER_RECENCY
    ]
    flags = [e.still_on_page for e in provider if e.still_on_page is not None]
    gone = bool(flags) and not any(flags)
    has_page_flags = bool(flags)
    valid = any(e.verification_status == EmailVerificationStatus.VALID for e in emails)
    invalid = bool(emails) and all(
        e.verification_status == EmailVerificationStatus.INVALID for e in emails
    )

    if invalid and (delisted or not provider or (has_page_flags and gone)):
        return (
            EmploymentStatus.FORMER,
            "Mailbox rejected and no source still lists the person",
        )
    if still_listed and (recent or valid):
        return (
            EmploymentStatus.CURRENT_LIKELY,
            "Provider sources still list the person and were seen recently",
        )
    if still_listed or (recent and valid):
        return EmploymentStatus.CURRENT_LIKELY, "Recent provider evidence of the current role"
    if valid and provider:
        return (
            EmploymentStatus.CURRENT_LIKELY,
            "Company mailbox verified; provider record present",
        )
    if delisted:
        return (
            EmploymentStatus.CURRENT_UNCERTAIN,
            "No longer listed on the company website page that named them",
        )
    if official:
        return (
            EmploymentStatus.CURRENT_UNCERTAIN,
            "Listed on the company website, but not re-checked recently",
        )
    if has_page_flags and gone:
        return (
            EmploymentStatus.CURRENT_UNCERTAIN,
            "Provider source pages no longer list the person",
        )
    return EmploymentStatus.CURRENT_UNCERTAIN, "No recent evidence confirming the current role"


def independent_sources(evidence: Sequence[EvidenceView]) -> int:
    keys = set()
    for item in evidence:
        # A verification result or a bare profile link does not corroborate the role.
        if item.source_type in {
            EvidenceSourceType.EMAIL_VERIFICATION,
            EvidenceSourceType.PUBLIC_PROFILE_URL,
        }:
            continue
        keys.add((item.source_type, (item.source_url or "").lower().rstrip("/")))
    return len(keys)


def suitability(
    role_match: RoleMatch,
    employment: EmploymentStatus,
    evidence: Sequence[EvidenceView],
    email: EmailView | None,
) -> tuple[Suitability, dict]:
    sources = independent_sources(evidence)
    evidence_points = 0 if sources == 0 else 5 if sources == 1 else 10 if sources == 2 else 15
    if email is None or email.verification_status == EmailVerificationStatus.INVALID:
        email_points = 0
    elif email.verification_status == EmailVerificationStatus.VALID:
        email_points = 15
    else:
        email_points = 7
    components = {
        "role": ROLE_POINTS[role_match],
        "employment": EMPLOYMENT_POINTS[employment],
        "evidence": evidence_points,
        "email": email_points,
    }
    score = sum(components.values())
    components["score"] = score
    components["independent_sources"] = sources

    if role_match == RoleMatch.NONE:
        grade, rule = Suitability.LOW, "Role does not match a target persona"
    elif employment == EmploymentStatus.FORMER:
        grade, rule = Suitability.LOW, "Evidence indicates a former employee"
    elif employment == EmploymentStatus.CONFLICTING:
        grade, rule = Suitability.INSUFFICIENT_EVIDENCE, "Sources conflict about the role"
    elif employment == EmploymentStatus.CURRENT_UNCERTAIN and sources < 2:
        grade, rule = (
            Suitability.INSUFFICIENT_EVIDENCE,
            "Only one source and no recent confirmation of the role",
        )
    elif score >= 70 and role_match == RoleMatch.HIGH:
        grade, rule = Suitability.HIGH, "Strong role match with current-employment evidence"
    elif score >= 45 and role_match in {RoleMatch.HIGH, RoleMatch.MEDIUM}:
        grade, rule = Suitability.MEDIUM, "Relevant role with adequate evidence"
    else:
        grade, rule = Suitability.LOW, "Weak role match or limited evidence"
    components["rule"] = rule
    return grade, components


def contact_status(
    role_match: RoleMatch,
    employment: EmploymentStatus,
    grade: Suitability,
    email: EmailView | None,
    review: str | None,
) -> ContactStatus:
    if review == ReviewStatus.EXCLUDED:
        return ContactStatus.EXCLUDED
    approved = review == ReviewStatus.APPROVED
    if not approved:
        if role_match in {RoleMatch.NONE, RoleMatch.LOW} or employment == EmploymentStatus.FORMER:
            return ContactStatus.NOT_RELEVANT
        if grade == Suitability.INSUFFICIENT_EVIDENCE:
            return ContactStatus.INSUFFICIENT_EVIDENCE
        if grade == Suitability.LOW:
            return ContactStatus.NOT_RELEVANT
    if email is None or email.verification_status == EmailVerificationStatus.INVALID:
        return ContactStatus.CONTACT_FOUND_EMAIL_MISSING
    if email.verification_status == EmailVerificationStatus.VALID:
        return ContactStatus.READY_FOR_DRAFT
    return ContactStatus.EMAIL_UNVERIFIED


def legacy_role_match(role_match: RoleMatch, confidence: int, review: str | None) -> BuyerRoleMatch:
    """Map to the older enum that campaign and outbound gates already enforce."""

    if review == ReviewStatus.EXCLUDED:
        return BuyerRoleMatch.UNKNOWN
    if role_match == RoleMatch.HIGH:
        return BuyerRoleMatch.EXACT if confidence >= 95 else BuyerRoleMatch.STRONG
    if role_match == RoleMatch.MEDIUM:
        return BuyerRoleMatch.PARTIAL
    if review == ReviewStatus.APPROVED:
        return BuyerRoleMatch.PARTIAL
    return BuyerRoleMatch.UNKNOWN


STATUS_ORDER = {
    ContactStatus.READY_FOR_DRAFT: 0,
    ContactStatus.EMAIL_UNVERIFIED: 1,
    ContactStatus.CONTACT_FOUND_EMAIL_MISSING: 2,
    ContactStatus.INSUFFICIENT_EVIDENCE: 3,
    ContactStatus.NOT_RELEVANT: 4,
    ContactStatus.EXCLUDED: 5,
}
SUITABILITY_ORDER = {
    Suitability.HIGH: 0,
    Suitability.MEDIUM: 1,
    Suitability.LOW: 2,
    Suitability.INSUFFICIENT_EVIDENCE: 3,
}
RANKABLE = {
    ContactStatus.READY_FOR_DRAFT,
    ContactStatus.EMAIL_UNVERIFIED,
    ContactStatus.CONTACT_FOUND_EMAIL_MISSING,
}


def normalize_linkedin(url: str | None) -> str | None:
    """Canonical public-profile URL, or None. The URL is stored as a reference, never fetched."""

    if not url:
        return None
    value = url.strip()
    if not re.match(r"^https?://", value, re.I):
        if value.lower().startswith(("linkedin.com/", "www.linkedin.com/")):
            value = "https://" + value
        elif re.fullmatch(r"[A-Za-z0-9\-_%]{3,100}", value):
            value = "https://www.linkedin.com/in/" + value
        else:
            return None
    match = re.match(
        r"^https?://(?:[a-z]{2,3}\.)?linkedin\.com/in/([A-Za-z0-9\-_%]{3,100})/?(?:[?#].*)?$",
        value,
        re.I,
    )
    if not match:
        return None
    return f"https://www.linkedin.com/in/{match.group(1).lower()}"
