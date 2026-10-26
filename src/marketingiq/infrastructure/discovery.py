"""Hunter Discover adapter: find companies by location, industry, headcount and keywords.

Discover calls do not consume Hunter credits. Free plans return the first page (up to 100
companies) only; paid plans may page with ``offset``.
"""

from __future__ import annotations

import os

import httpx

from marketingiq.domain.discovery import (
    HEADCOUNT_BUCKETS,
    DiscoveredCompany,
    DiscoveryCriteria,
    DiscoveryResult,
)
from marketingiq.domain.providers import (
    ProviderAuthenticationError,
    ProviderError,
    ProviderMalformedResponse,
    ProviderNotConfigured,
    ProviderRateLimited,
)

DISCOVER_URL = "https://api.hunter.io/v2/discover"
PAGE_SIZE = 100


class DiscoveryInvalidFilter(ProviderError):
    category = "INVALID_FILTER"

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class DiscoveryPaginationUnavailable(ProviderError):
    category = "PAGINATION_UNAVAILABLE"


class DiscoveryAccessDenied(ProviderError):
    category = "NO_DISCOVER_ACCESS"


def discover_request_body(criteria: DiscoveryCriteria) -> dict:
    if criteria.query:
        return {"query": criteria.query[:500]}
    body: dict = {}
    if criteria.countries:
        body["headquarters_location"] = {
            "include": [{"country": code.strip().upper()} for code in criteria.countries]
        }
    if criteria.industries:
        body["industry"] = {"include": list(criteria.industries)}
    headcounts = [value for value in criteria.headcounts if value in HEADCOUNT_BUCKETS]
    if headcounts:
        body["headcount"] = headcounts
    if criteria.keywords:
        body["keywords"] = {"include": list(criteria.keywords), "match": "any"}
    return body


class HunterDiscoveryProvider:
    key = "HUNTER"

    def __init__(self, api_key: str | None = None, client: httpx.Client | None = None) -> None:
        self._api_key = api_key if api_key is not None else os.environ.get("HUNTER_API_KEY")
        self.client = client

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def discover(self, criteria: DiscoveryCriteria, offset: int = 0) -> DiscoveryResult:
        if not self.configured:
            raise ProviderNotConfigured("Hunter is not configured")
        if criteria.empty:
            raise ValueError("At least one discovery filter or a query is required")
        body = discover_request_body(criteria)
        if offset > 0:
            body["limit"] = PAGE_SIZE
            body["offset"] = min(offset, 10000)
        client = self.client or httpx.Client(timeout=20)
        try:
            response = client.post(
                DISCOVER_URL,
                json=body,
                headers={"X-API-KEY": self._api_key or "", "Accept": "application/json"},
            )
        except httpx.HTTPError as error:
            raise ProviderError("Hunter Discover request failed") from error
        if response.status_code == 429:
            raise ProviderRateLimited("Hunter rate limit reached")
        if response.status_code == 401:
            raise ProviderAuthenticationError("Hunter authentication failed")
        if response.status_code in {400, 403}:
            code = _error_code(response)
            if code == "pagination_error":
                raise DiscoveryPaginationUnavailable("Hunter plan does not allow pagination")
            if code == "no_discover_access" or response.status_code == 403:
                raise DiscoveryAccessDenied("Hunter plan does not include Discover")
            raise DiscoveryInvalidFilter(f"Hunter rejected a filter ({code})", code)
        if response.status_code >= 400:
            raise ProviderError(f"Hunter Discover failed with HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as error:
            raise ProviderMalformedResponse("Hunter returned an invalid response") from error
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            raise ProviderMalformedResponse("Hunter returned a malformed Discover response")
        companies = []
        for item in data:
            if not isinstance(item, dict):
                continue
            domain = str(item.get("domain") or "").strip().lower()
            name = str(item.get("organization") or "").strip() or domain
            if domain:
                companies.append(DiscoveredCompany(domain=domain, name=name[:255]))
        meta = payload.get("meta") or {}
        total = meta.get("results")
        return DiscoveryResult(
            provider_key=self.key,
            companies=tuple(companies),
            total_results=total if isinstance(total, int) else None,
        )


def _error_code(response) -> str:
    try:
        errors = response.json().get("errors") or []
        if errors and isinstance(errors[0], dict):
            return str(errors[0].get("id") or errors[0].get("code") or "unknown")
    except (ValueError, AttributeError):
        pass
    return "unknown"
