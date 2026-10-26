from __future__ import annotations

import ipaddress
import os
import socket
from collections.abc import Callable
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

from marketingiq.domain.models import DataClassification, RedistributionStatus
from marketingiq.domain.providers import (
    ContactProviderResult,
    ProviderAuthenticationError,
    ProviderCapability,
    ProviderContact,
    ProviderContactSource,
    ProviderError,
    ProviderFact,
    ProviderMalformedResponse,
    ProviderNotConfigured,
    ProviderRateLimited,
    ProviderResult,
)

MAX_PUBLIC_BYTES = 1_000_000
MAX_REDIRECTS = 3
USER_AGENT = "MarketingIQ-PublicResearch/1.0 (+https://marketingiq.invalid/research-policy)"


class UnsafePublicUrl(ProviderError):
    category = "UNSAFE_URL"


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title_parts: list[str] = []
        self.description: str | None = None
        self.links: list[tuple[str, str]] = []
        self._title = False
        self._anchor: str | None = None
        self._anchor_text: list[str] = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "title":
            self._title = True
        elif tag == "meta" and values.get("name", "").lower() == "description":
            self.description = values.get("content")
        elif tag == "a" and values.get("href"):
            self._anchor = values["href"]
            self._anchor_text = []

    def handle_endtag(self, tag):
        if tag == "title":
            self._title = False
        elif tag == "a" and self._anchor:
            self.links.append((self._anchor, " ".join(self._anchor_text).strip()))
            self._anchor = None

    def handle_data(self, data):
        if self._title:
            self.title_parts.append(data)
        if self._anchor:
            self._anchor_text.append(data)


def _system_resolver(host: str) -> set[str]:
    return {item[4][0] for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)}


def validate_public_url(url: str, resolver: Callable[[str], set[str]] = _system_resolver) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise UnsafePublicUrl("Only HTTP(S) public URLs are permitted")
    if not parsed.hostname or parsed.username or parsed.password or parsed.port:
        raise UnsafePublicUrl("URL hosts must not contain credentials or custom ports")
    try:
        addresses = resolver(parsed.hostname)
    except OSError as error:
        raise ProviderError("Public host could not be resolved") from error
    if not addresses:
        raise UnsafePublicUrl("Public host did not resolve")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise UnsafePublicUrl("Local, private, and reserved networks are not permitted")


class PublicWebProvider:
    key = "PUBLIC_WEB"
    capabilities = frozenset({ProviderCapability.ENRICH_COMPANY})
    costs_credits = False

    def __init__(
        self,
        client: httpx.Client | None = None,
        resolver: Callable[[str], set[str]] = _system_resolver,
        max_bytes: int = MAX_PUBLIC_BYTES,
    ) -> None:
        self.client = client or httpx.Client(timeout=10, follow_redirects=False)
        self.resolver = resolver
        self.max_bytes = max_bytes

    @property
    def configured(self) -> bool:
        return True

    def _fetch(
        self, url: str, allowed_hosts: frozenset[str] | None = None
    ) -> tuple[str, str, datetime]:
        for _ in range(MAX_REDIRECTS + 1):
            if allowed_hosts is not None and (urlsplit(url).hostname or "").lower() not in (
                allowed_hosts
            ):
                raise UnsafePublicUrl("Redirect left the permitted site")
            validate_public_url(url, self.resolver)
            try:
                response = self.client.get(url, headers={"User-Agent": USER_AGENT})
            except httpx.HTTPError as error:
                raise ProviderError("Public page request failed") from error
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise ProviderError("Redirect response omitted Location")
                url = urljoin(url, location)
                continue
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as error:
                raise ProviderError(f"Public page returned HTTP {response.status_code}") from error
            content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            if content_type not in {"text/html", "application/xhtml+xml"}:
                raise ProviderError("Public research accepts HTML only")
            declared = response.headers.get("content-length")
            if declared and int(declared) > self.max_bytes:
                raise ProviderError("Public response exceeds content-size limit")
            content = response.content
            if len(content) > self.max_bytes:
                raise ProviderError("Public response exceeds content-size limit")
            return (
                str(response.url),
                content.decode(response.encoding or "utf-8", "replace"),
                datetime.now(UTC),
            )
        raise UnsafePublicUrl("Public response exceeded redirect limit")

    def enrich_company(self, normalized_domain: str) -> ProviderResult:
        homepage_url, html, retrieved = self._fetch(f"https://{normalized_domain}/")
        parser = _PageParser()
        parser.feed(html)
        facts: list[ProviderFact] = []
        description = (parser.description or "").strip()
        if description:
            facts.append(
                ProviderFact("company_description", description, 80, homepage_url, retrieved)
            )
        title = " ".join(parser.title_parts).strip()
        if title:
            facts.append(ProviderFact("website_title", title, 90, homepage_url, retrieved))

        # Follow only clearly labelled same-domain company pages, never more than three.
        selected: list[tuple[str, str]] = []
        labels = {"about", "about us", "careers", "jobs", "contact", "locations"}
        for href, text in parser.links:
            candidate = urljoin(homepage_url, href)
            parsed = urlsplit(candidate)
            if (
                parsed.hostname == normalized_domain
                or parsed.hostname == f"www.{normalized_domain}"
            ):
                label = text.lower().strip()
                if label in labels and candidate not in [url for url, _ in selected]:
                    selected.append((candidate, label))
            if len(selected) == 3:
                break
        for candidate, label in selected:
            page_url, page_html, page_retrieved = self._fetch(candidate)
            if label in {"careers", "jobs"}:
                facts.append(
                    ProviderFact(
                        "active_hiring",
                        True,
                        75,
                        page_url,
                        page_retrieved,
                        "A clearly linked careers/jobs page was retrieved",
                    )
                )
            page = _PageParser()
            page.feed(page_html)
            if label in {"about", "about us"} and page.description:
                facts.append(
                    ProviderFact(
                        "company_description",
                        page.description.strip(),
                        85,
                        page_url,
                        page_retrieved,
                    )
                )
        return ProviderResult(provider_key=self.key, facts=tuple(facts))


HUNTER_DEPARTMENTS = frozenset(
    {
        "executive", "it", "finance", "management", "sales", "legal", "support", "hr",
        "marketing", "communication", "education", "design", "health", "operations",
        "product", "research", "consulting", "administrative", "procurement",
    }
)
HUNTER_SENIORITIES = frozenset({"junior", "senior", "executive"})


def _text(value) -> str | None:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return None


class HunterProvider:
    key = "HUNTER"
    capabilities = frozenset(
        {
            ProviderCapability.ENRICH_COMPANY,
            ProviderCapability.SEARCH_CONTACTS,
            ProviderCapability.FIND_EMAIL,
            ProviderCapability.VERIFY_EMAIL,
        }
    )
    costs_credits = True

    def __init__(self, api_key: str | None = None, client: httpx.Client | None = None) -> None:
        self._api_key = api_key if api_key is not None else os.environ.get("HUNTER_API_KEY")
        self.client = client

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def enrich_company(self, normalized_domain: str) -> ProviderResult:
        """Company facts from Hunter Company Enrichment (GET /v2/companies/find).

        Domain Search only returns the organisation *name* (a string), so it cannot provide
        company facts. A 404 means Hunter has no profile for the domain: no facts, no error.
        """
        if not self.configured:
            raise ProviderNotConfigured("Hunter is not configured")
        client = self.client or httpx.Client(timeout=10)
        try:
            response = client.get(
                "https://api.hunter.io/v2/companies/find",
                params={"domain": normalized_domain, "api_key": self._api_key},
            )
        except httpx.HTTPError as error:
            raise ProviderError("Hunter request failed") from error
        if response.status_code == 404:
            return ProviderResult(
                provider_key=self.key,
                facts=(),
                classification=DataClassification.THIRD_PARTY_LICENSED,
                redistribution_status=RedistributionStatus.UNKNOWN,
                request_identifier=response.headers.get("x-request-id"),
            )
        if response.status_code == 429:
            raise ProviderRateLimited("Hunter rate limit reached")
        if response.status_code in {401, 403}:
            raise ProviderAuthenticationError("Hunter authentication failed")
        if response.status_code >= 400:
            raise ProviderError(f"Hunter request failed with HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as error:
            raise ProviderMalformedResponse("Hunter returned an invalid response") from error
        if not isinstance(payload, dict):
            raise ProviderMalformedResponse("Hunter returned a malformed response")
        data = payload.get("data")
        data = data if isinstance(data, dict) else {}

        def part(key: str) -> dict:
            value = data.get(key)
            return value if isinstance(value, dict) else {}

        category, geo, metrics = part("category"), part("geo"), part("metrics")
        country_code = _text(geo.get("countryCode"))
        country = (
            country_code.upper()
            if country_code and len(country_code) == 2
            else _text(geo.get("country"))
        )
        employee_count = metrics.get("employeesCount")
        retrieved = datetime.now(UTC)
        source = f"hunter:company:{normalized_domain}"
        mapping = {
            "company_description": _text(data.get("description")),
            "industry": _text(category.get("industry"))
            or _text(category.get("industryGroup"))
            or _text(category.get("sector")),
            "country": country,
            "headquarters": _text(data.get("location")),
            "employee_range": _text(metrics.get("employees")),
            "employee_count": employee_count
            if isinstance(employee_count, int) and employee_count > 0
            else None,
        }
        facts = tuple(
            ProviderFact(key, value, 80, None, retrieved, source)
            for key, value in mapping.items()
            if value not in (None, "", [])
        )
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        params = meta.get("params") if isinstance(meta.get("params"), dict) else {}
        credits = meta.get("credits") if isinstance(meta.get("credits"), dict) else {}
        return ProviderResult(
            provider_key=self.key,
            facts=facts,
            classification=DataClassification.THIRD_PARTY_LICENSED,
            redistribution_status=RedistributionStatus.UNKNOWN,
            request_identifier=response.headers.get("x-request-id"),
            credits_used=params.get("credits_used"),
            credits_remaining=credits.get("available"),
        )

    def _request(self, path: str, params: dict) -> tuple[dict, object]:
        if not self.configured:
            raise ProviderNotConfigured("Hunter is not configured")
        try:
            response = (self.client or httpx.Client(timeout=10)).get(
                f"https://api.hunter.io/v2/{path}", params={**params, "api_key": self._api_key}
            )
        except httpx.HTTPError as error:
            raise ProviderError("Hunter request failed") from error
        if response.status_code == 429:
            raise ProviderRateLimited("Hunter rate limit reached")
        if response.status_code in {401, 403}:
            raise ProviderAuthenticationError("Hunter authentication failed")
        if response.status_code >= 400:
            raise ProviderError(f"Hunter request failed with HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as error:
            raise ProviderMalformedResponse("Hunter returned an invalid response") from error
        if not isinstance(payload, dict) or not isinstance(payload.get("data", {}), dict | list):
            raise ProviderMalformedResponse("Hunter returned a malformed response")
        return payload, response

    @staticmethod
    def _contact(item: dict) -> ProviderContact:
        if not isinstance(item, dict):
            raise ProviderMalformedResponse("Hunter returned a malformed contact")
        first, last = _text(item.get("first_name")), _text(item.get("last_name"))
        sources = []
        for source in item.get("sources") or []:
            if isinstance(source, dict):
                sources.append(
                    ProviderContactSource(
                        uri=_text(source.get("uri")),
                        domain=_text(source.get("domain")),
                        extracted_on=_text(source.get("extracted_on")),
                        last_seen_on=_text(source.get("last_seen_on")),
                        still_on_page=source.get("still_on_page")
                        if isinstance(source.get("still_on_page"), bool)
                        else None,
                    )
                )
        verification = item.get("verification")
        if not isinstance(verification, dict):
            verification = {}
        return ProviderContact(
            reference=str(item.get("id")) if item.get("id") is not None else None,
            first_name=first,
            last_name=last,
            full_name=" ".join(x for x in (first, last) if x) or None,
            job_title=_text(item.get("position")),
            department=_text(item.get("department")),
            seniority=_text(item.get("seniority")),
            email=_text(item.get("value")),
            confidence=item.get("confidence") if isinstance(item.get("confidence"), int) else None,
            position_raw=_text(item.get("position_raw")),
            decision_maker=item.get("decision_maker")
            if isinstance(item.get("decision_maker"), bool)
            else None,
            linkedin=_text(item.get("linkedin")) or _text(item.get("linkedin_url")),
            email_type=_text(item.get("type")),
            sources=tuple(sources[:20]),
            verification_status=_text(verification.get("status")),
            verification_date=_text(verification.get("date")),
        )

    @staticmethod
    def _result(
        payload: dict, response, contacts: tuple[ProviderContact, ...]
    ) -> ContactProviderResult:
        meta = payload.get("meta") or {}
        return ContactProviderResult(
            "HUNTER",
            contacts,
            response.headers.get("x-request-id"),
            meta.get("params", {}).get("credits_used"),
            meta.get("credits", {}).get("available"),
        )

    def search_contacts(self, company_identifier: str, max_results: int) -> ContactProviderResult:
        payload, response = self._request(
            "domain-search", {"domain": company_identifier, "limit": max_results}
        )
        emails = (payload.get("data") or {}).get("emails", [])
        if not isinstance(emails, list):
            raise ProviderMalformedResponse("Hunter returned malformed contacts")
        return self._result(
            payload, response, tuple(self._contact(x) for x in emails[:max_results])
        )

    def search_decision_makers(
        self,
        company_identifier: str,
        *,
        departments: list[str] | tuple[str, ...] = (),
        seniorities: list[str] | tuple[str, ...] = (),
        max_results: int = 25,
    ) -> ContactProviderResult:
        """One filtered Domain Search for named people (Hunter charges per call with results)."""
        params: dict = {
            "domain": company_identifier,
            "limit": max(1, min(int(max_results), 100)),
            "type": "personal",
        }
        departments = [x for x in departments if x in HUNTER_DEPARTMENTS]
        seniorities = [x for x in seniorities if x in HUNTER_SENIORITIES]
        if departments:
            params["department"] = ",".join(sorted(set(departments)))
        if seniorities:
            params["seniority"] = ",".join(sorted(set(seniorities)))
        payload, response = self._request("domain-search", params)
        emails = (payload.get("data") or {}).get("emails", [])
        if not isinstance(emails, list):
            raise ProviderMalformedResponse("Hunter returned malformed contacts")
        return self._result(
            payload, response, tuple(self._contact(x) for x in emails[: params["limit"]])
        )

    def find_email(self, person: dict) -> ContactProviderResult:
        payload, response = self._request("email-finder", person)
        data = payload.get("data") or {}
        if data.get("email"):
            data = {**data, "value": data["email"]}
        return self._result(payload, response, (self._contact(data),) if data.get("email") else ())

    def verify_email(self, email: str) -> ContactProviderResult:
        payload, response = self._request("email-verifier", {"email": email})
        data = payload.get("data") or {}
        if not isinstance(data, dict):
            raise ProviderMalformedResponse("Hunter returned malformed verification")
        contact = ProviderContact(
            email=email,
            confidence=data.get("score") if isinstance(data.get("score"), int) else None,
            reference=data.get("status"),
        )
        return self._result(payload, response, (contact,))
