from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from marketingiq.domain.models import DataClassification, RedistributionStatus


class ProviderCapability(enum.StrEnum):
    SEARCH_COMPANIES = "search_companies"
    ENRICH_COMPANY = "enrich_company"
    SEARCH_CONTACTS = "search_contacts"
    FIND_EMAIL = "find_email"
    VERIFY_EMAIL = "verify_email"


class ProviderError(RuntimeError):
    category = "PROVIDER_ERROR"


class ProviderNotConfigured(ProviderError):
    category = "NOT_CONFIGURED"


class ProviderRateLimited(ProviderError):
    category = "RATE_LIMITED"


@dataclass(frozen=True)
class ProviderFact:
    key: str
    value: Any
    confidence: int
    source_url: str | None
    retrieved_at: datetime
    reference_text: str | None = None


@dataclass(frozen=True)
class ProviderResult:
    provider_key: str
    facts: tuple[ProviderFact, ...] = ()
    classification: DataClassification = DataClassification.PUBLIC_EVIDENCE
    redistribution_status: RedistributionStatus = RedistributionStatus.UNKNOWN
    request_identifier: str | None = None
    credits_used: int | None = None
    credits_remaining: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class CompanyEnrichmentProvider(Protocol):
    key: str
    capabilities: frozenset[ProviderCapability]
    costs_credits: bool

    @property
    def configured(self) -> bool: ...

    def enrich_company(self, normalized_domain: str) -> ProviderResult: ...


# Capability contracts retained for future adapters; Sprint 2 Task 1 implements enrichment only.
class CompanySearchProvider(Protocol):
    def search_companies(self, criteria: dict[str, Any]) -> ProviderResult: ...


class ContactSearchProvider(Protocol):
    def search_contacts(self, company_identifier: str) -> ProviderResult: ...


class EmailProvider(Protocol):
    def find_email(self, person: dict[str, Any]) -> ProviderResult: ...

    def verify_email(self, email: str) -> ProviderResult: ...
