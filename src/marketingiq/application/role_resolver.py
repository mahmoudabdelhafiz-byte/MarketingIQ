"""Map observed job titles to a product's target personas.

Rules run first and are deterministic. Only titles the rules cannot settle are sent (in one
batch) to the optional AI resolver, whose output is validated and can never introduce a person,
title or persona that was not supplied. Any AI failure falls back to the rule result.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from marketingiq.domain.decision_makers import RoleMatch

# Abbreviations that expand unambiguously. Ambiguous ones (CPO, CCO, CDO) are deliberately absent
# so they are routed to the AI resolver instead of being guessed.
ABBREVIATIONS: dict[str, str] = {
    "ceo": "chief executive officer",
    "cio": "chief information officer",
    "cto": "chief technology officer",
    "chro": "chief human resources officer",
    "cfo": "chief financial officer",
    "coo": "chief operating officer",
    "cmo": "chief marketing officer",
    "ciso": "chief information security officer",
    "md": "managing director",
    "gm": "general manager",
    "vp": "vice president",
    "svp": "senior vice president",
    "evp": "executive vice president",
    "avp": "assistant vice president",
    "hr": "human resources",
    "it": "information technology",
    "ict": "information and communications technology",
    "pr": "public relations",
    "dir": "director",
    "mgr": "manager",
    "sr": "senior",
    "snr": "senior",
    "jr": "junior",
    "asst": "assistant",
    "assoc": "associate",
    "dept": "department",
    "hod": "head of department",
}
AMBIGUOUS_ACRONYMS = frozenset({"cpo", "cco", "cdo", "cso", "cao", "cxo", "cbo"})
STOPWORDS = frozenset({"of", "and", "the", "for", "to", "in", "at", "a", "an", "our"})
# Words that mean the person supports, rather than holds, a role.
SUPPORT_QUALIFIERS = frozenset(
    {
        "assistant",
        "associate",
        "intern",
        "internship",
        "trainee",
        "coordinator",
        "secretary",
        "analyst",
        "specialist",
        "representative",
        "student",
        "apprentice",
    }
)
FORMER_MARKERS = re.compile(r"\b(former|formerly|previously|retired)\b|(^|\s)ex-\s?\w", re.I)
SUPPORT_PHRASES = re.compile(r"\b(assistant|advisor|adviser|consultant)\s+to\b|\boffice of\b", re.I)

# Persona vocabulary → Hunter Domain Search filter values.
DEPARTMENT_KEYWORDS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("information technology", "technology", "digital", "it", "ict", "systems"), ("it",)),
    (("human resources", "hr", "people", "talent", "personnel"), ("hr",)),
    (("executive", "leadership", "c suite", "board"), ("executive",)),
    (("management", "general management", "operations management"), ("management",)),
    (("marketing", "brand"), ("marketing",)),
    (
        ("communications", "communication", "public relations", "pr", "corporate affairs"),
        ("communication",),
    ),
    (("finance", "financial"), ("finance",)),
    (("operations",), ("operations",)),
    (("sales", "commercial", "business development"), ("sales",)),
    (("legal", "compliance"), ("legal",)),
    (("procurement", "purchasing"), ("procurement",)),
    (("administration", "admin", "facilities"), ("administrative",)),
    (("security", "information security", "cybersecurity"), ("it",)),
    (("product",), ("product",)),
)
SENIORITY_KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        (
            "c level",
            "c suite",
            "chief",
            "executive",
            "vice president",
            "president",
            "owner",
            "founder",
            "partner",
            "board",
            "managing director",
            "general manager",
        ),
        "executive",
    ),
    (("director", "head", "manager", "senior", "lead"), "senior"),
    (("junior", "entry", "associate"), "junior"),
)
DECISION_TITLE_WORDS = frozenset(
    {
        "chief",
        "officer",
        "director",
        "head",
        "manager",
        "president",
        "founder",
        "owner",
        "partner",
        "chairman",
        "chairwoman",
        "chair",
        "lead",
        "executive",
        "general",
        "managing",
        "principal",
        "vice",
    }
)
# Role-level words that say how senior someone is but not which function they lead.
GENERIC_ROLE_WORDS = frozenset(
    {
        "chief",
        "officer",
        "head",
        "director",
        "manager",
        "vice",
        "president",
        "senior",
        "lead",
        "group",
        "global",
        "regional",
        "deputy",
        "acting",
        "general",
        "principal",
        "department",
        "team",
        "leader",
        "leadership",
    }
)
_ROLE_RANK = {RoleMatch.HIGH: 3, RoleMatch.MEDIUM: 2, RoleMatch.LOW: 1, RoleMatch.NONE: 0}


def normalize_text(value: str | None) -> str:
    """Lower-case, strip punctuation and expand unambiguous abbreviations."""

    text = (value or "").lower().replace("&", " and ").replace("/", " ")
    text = re.sub(r"\bl\s*and\s*d\b", "learning and development", text)
    text = re.sub(r"\bc[\s-]?suite\b", "c suite", text)
    text = re.sub(r"\bc[\s-]?level\b", "c level", text)
    tokens = re.findall(r"[a-z0-9]+", text)
    expanded: list[str] = []
    for token in tokens:
        expanded.extend(ABBREVIATIONS.get(token, token).split())
    return " ".join(expanded)


def _tokens(value: str) -> list[str]:
    return [t for t in value.split() if t not in STOPWORDS]


def _contains_phrase(haystack: str, needle: str) -> bool:
    return bool(needle) and re.search(rf"(^| ){re.escape(needle)}( |$)", haystack) is not None


def hunter_departments(values: Iterable[str]) -> list[str]:
    result: set[str] = set()
    for value in values:
        normalized = normalize_text(value)
        for keywords, mapped in DEPARTMENT_KEYWORDS:
            if any(_contains_phrase(normalized, normalize_text(k)) for k in keywords):
                result.update(mapped)
    return sorted(result)


def hunter_seniorities(values: Iterable[str]) -> list[str]:
    result: set[str] = set()
    for value in values:
        normalized = normalize_text(value)
        for keywords, mapped in SENIORITY_KEYWORDS:
            if any(_contains_phrase(normalized, normalize_text(k)) for k in keywords):
                result.add(mapped)
                break
    return sorted(result)


@dataclass(frozen=True)
class PersonaSpec:
    id: str | None
    name: str
    titles: tuple[str, ...] = ()
    departments: tuple[str, ...] = ()
    seniorities: tuple[str, ...] = ()
    max_contacts: int = 2
    priority: int = 100

    @property
    def normalized_titles(self) -> tuple[str, ...]:
        seen: list[str] = []
        for title in (self.name, *self.titles):
            value = normalize_text(title)
            if value and value not in seen:
                seen.append(value)
        return tuple(seen)

    @property
    def hunter_departments(self) -> list[str]:
        return hunter_departments(self.departments or (self.name,))

    @property
    def hunter_seniorities(self) -> list[str]:
        return hunter_seniorities(self.seniorities)


@dataclass(frozen=True)
class PersonCandidate:
    key: str
    title: str | None
    department: str | None = None
    seniority: str | None = None


@dataclass(frozen=True)
class RoleResolution:
    persona: PersonaSpec | None
    normalized_role: str
    role_match: RoleMatch
    confidence: int
    reason: str
    resolver: str = "RULES"
    ambiguous: bool = False

    @property
    def rank(self) -> int:
        return _ROLE_RANK[self.role_match]


def _match_one(candidate: PersonCandidate, persona: PersonaSpec) -> RoleResolution:
    title = normalize_text(candidate.title)
    raw_tokens = set(re.findall(r"[a-z0-9]+", (candidate.title or "").lower()))
    department = normalize_text(candidate.department)
    seniority = normalize_text(candidate.seniority)
    ambiguous = bool(raw_tokens & AMBIGUOUS_ACRONYMS)

    if title and FORMER_MARKERS.search(candidate.title or ""):
        return RoleResolution(
            persona, title, RoleMatch.NONE, 90, "Title describes a former or past role"
        )

    support = bool(set(title.split()) & SUPPORT_QUALIFIERS) or bool(
        SUPPORT_PHRASES.search(candidate.title or "")
    )
    persona_departments = set(persona.hunter_departments)
    department_hit = bool(department) and (
        bool(set(hunter_departments([department])) & persona_departments)
        or any(_contains_phrase(department, normalize_text(d)) for d in persona.departments)
    )
    # A function word in the title itself ("Chief People Officer") also counts.
    department_hit = department_hit or any(
        _contains_phrase(title, normalize_text(d))
        for d in persona.departments
        if normalize_text(d)
        and normalize_text(d) not in GENERIC_ROLE_WORDS
        and normalize_text(d) not in {"executive", "management"}
    )
    wanted_seniority = set(persona.hunter_seniorities)
    seniority_hit = bool(seniority) and bool(
        set(hunter_seniorities([seniority])) & wanted_seniority
    )

    if title:
        for alias in persona.normalized_titles:
            if title == alias and not support:
                return RoleResolution(
                    persona, alias, RoleMatch.HIGH, 95, f"Title matches the '{persona.name}' alias"
                )
        for alias in persona.normalized_titles:
            if _contains_phrase(title, alias):
                if support:
                    return RoleResolution(
                        persona,
                        alias,
                        RoleMatch.LOW,
                        60,
                        "Title supports the role rather than holding it",
                        ambiguous=True,
                    )
                deputy = "deputy" in title.split() or "acting" in title.split()
                return RoleResolution(
                    persona,
                    alias,
                    RoleMatch.MEDIUM if deputy else RoleMatch.HIGH,
                    75 if deputy else 85,
                    ("Deputy/acting holder of " if deputy else "Title contains ")
                    + f"the '{persona.name}' role",
                )
        best_overlap = 0.0
        best_alias = ""
        title_tokens = set(_tokens(title))
        for alias in persona.normalized_titles:
            alias_tokens = set(_tokens(alias))
            shared = alias_tokens & title_tokens
            if alias_tokens and (shared - GENERIC_ROLE_WORDS or alias_tokens <= GENERIC_ROLE_WORDS):
                overlap = len(shared) / len(alias_tokens)
                if overlap > best_overlap:
                    best_overlap, best_alias = overlap, alias
        senior_title = bool(title_tokens & DECISION_TITLE_WORDS)
        if best_overlap == 1.0 and not support:
            exact_set = title_tokens == set(_tokens(best_alias))
            return RoleResolution(
                persona,
                best_alias,
                RoleMatch.HIGH,
                90 if exact_set else 80,
                f"Title contains every term of the '{persona.name}' alias"
                + (" (different word order)" if exact_set else ""),
            )
        if best_overlap >= 2 / 3 and senior_title and not support:
            return RoleResolution(
                persona,
                best_alias,
                RoleMatch.MEDIUM,
                65,
                f"Most '{persona.name}' title terms appear in the title",
                ambiguous=True,
            )
        if department_hit and senior_title and not support:
            return RoleResolution(
                persona,
                title,
                RoleMatch.MEDIUM,
                60,
                f"Senior title in the {persona.name} department",
                ambiguous=True,
            )
        if department_hit or (best_overlap >= 1 / 3 and senior_title):
            return RoleResolution(
                persona,
                title,
                RoleMatch.LOW,
                40,
                "Only partial title or department alignment",
                ambiguous=True,
            )
        return RoleResolution(
            persona,
            title,
            RoleMatch.NONE,
            50,
            "Title does not align with the persona",
            ambiguous=ambiguous or senior_title,
        )

    if department_hit and seniority_hit:
        return RoleResolution(
            persona,
            department,
            RoleMatch.LOW,
            35,
            "No title; department and seniority align",
        )
    return RoleResolution(persona, "", RoleMatch.NONE, 20, "No title evidence")


def resolve_by_rules(candidate: PersonCandidate, personas: Sequence[PersonaSpec]) -> RoleResolution:
    best: RoleResolution | None = None
    for persona in sorted(personas, key=lambda p: (p.priority, p.name)):
        result = _match_one(candidate, persona)
        if best is None or (result.rank, result.confidence) > (best.rank, best.confidence):
            best = result
    if best is None:
        return RoleResolution(None, "", RoleMatch.NONE, 0, "No personas configured")
    if best.role_match == RoleMatch.NONE:
        return RoleResolution(
            None,
            best.normalized_role,
            RoleMatch.NONE,
            best.confidence,
            best.reason,
            ambiguous=best.ambiguous,
        )
    return best


class RoleAIClient(Protocol):
    model: str

    @property
    def configured(self) -> bool: ...

    def resolve_roles(
        self, candidates: list[dict[str, Any]], personas: list[dict[str, Any]]
    ) -> list[dict[str, Any]]: ...


@dataclass
class ResolverReport:
    ai_attempted: bool = False
    ai_succeeded: bool = False
    ai_error: str | None = None
    ai_resolved: int = 0
    notes: list[str] = field(default_factory=list)


def _clean_reason(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:280] or "AI classification based on the supplied title"


class RoleResolver:
    """Rules first; one batched AI call for ambiguous titles when a client is configured."""

    MAX_AI_CANDIDATES = 40

    def __init__(self, ai_client: RoleAIClient | None = None) -> None:
        self.ai_client = ai_client
        self.report = ResolverReport()

    def resolve_many(
        self, candidates: Sequence[PersonCandidate], personas: Sequence[PersonaSpec]
    ) -> dict[str, RoleResolution]:
        results = {c.key: resolve_by_rules(c, personas) for c in candidates}
        ambiguous = [
            c
            for c in candidates
            if c.title and results[c.key].ambiguous and results[c.key].role_match != RoleMatch.HIGH
        ][: self.MAX_AI_CANDIDATES]
        if not ambiguous or not personas or not self.ai_client or not self.ai_client.configured:
            return results
        self.report.ai_attempted = True
        by_name = {p.name.lower(): p for p in personas}
        try:
            raw = self.ai_client.resolve_roles(
                [
                    {
                        "candidate_id": c.key,
                        "title": c.title,
                        "department": c.department,
                        "seniority": c.seniority,
                    }
                    for c in ambiguous
                ],
                [
                    {
                        "name": p.name,
                        "titles": list(p.titles),
                        "departments": list(p.departments),
                        "seniorities": list(p.seniorities),
                    }
                    for p in personas
                ],
            )
        except Exception as error:  # noqa: BLE001 - any AI failure falls back to rules
            self.report.ai_error = getattr(error, "code", None) or type(error).__name__
            return results
        allowed = {c.key for c in ambiguous}
        resolved = 0
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict):
                continue
            key = item.get("candidate_id")
            if key not in allowed:
                continue  # never accept people the model was not given
            try:
                match = RoleMatch(str(item.get("role_match", "")).upper())
            except ValueError:
                continue
            persona_name = item.get("persona")
            persona = by_name.get(str(persona_name).lower()) if persona_name else None
            if match != RoleMatch.NONE and persona is None:
                continue  # a match must name one of the configured personas
            try:
                confidence = int(item.get("confidence", 0))
            except (TypeError, ValueError):
                confidence = 0
            confidence = max(0, min(confidence, 100))
            # The AI may refine, but an AI "HIGH" is capped below an exact alias match.
            confidence = min(confidence, 90)
            normalized_role = normalize_text(str(item.get("normalized_role") or ""))[:200]
            results[key] = RoleResolution(
                persona if match != RoleMatch.NONE else None,
                normalized_role or results[key].normalized_role,
                match,
                confidence,
                _clean_reason(item.get("reason")),
                resolver="AI",
            )
            allowed.discard(key)
            resolved += 1
        self.report.ai_succeeded = True
        self.report.ai_resolved = resolved
        return results
