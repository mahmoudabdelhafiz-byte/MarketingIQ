from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ProviderResult:
    records: tuple[dict[str, Any], ...]
    provider_key: str


class CompanySearchProvider(Protocol):
    def search_companies(self, criteria: dict[str, Any]) -> ProviderResult: ...


class CompanyEnrichmentProvider(Protocol):
    def enrich_company(self, company_identifier: str) -> ProviderResult: ...


class ContactSearchProvider(Protocol):
    def search_contacts(self, company_identifier: str) -> ProviderResult: ...


class EmailProvider(Protocol):
    def find_email(self, person: dict[str, Any]) -> ProviderResult: ...

    def verify_email(self, email: str) -> ProviderResult: ...
