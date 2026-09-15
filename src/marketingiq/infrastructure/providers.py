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
    ProviderCapability,
    ProviderError,
    ProviderFact,
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

    def _fetch(self, url: str) -> tuple[str, str, datetime]:
        for _ in range(MAX_REDIRECTS + 1):
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


class HunterProvider:
    key = "HUNTER"
    capabilities = frozenset({ProviderCapability.ENRICH_COMPANY})
    costs_credits = True

    def __init__(self, api_key: str | None = None, client: httpx.Client | None = None) -> None:
        self._api_key = api_key if api_key is not None else os.environ.get("HUNTER_API_KEY")
        self.client = client

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def enrich_company(self, normalized_domain: str) -> ProviderResult:
        if not self.configured:
            raise ProviderNotConfigured("Hunter is not configured")
        client = self.client or httpx.Client(timeout=10)
        try:
            response = client.get(
                "https://api.hunter.io/v2/domain-search",
                params={"domain": normalized_domain, "api_key": self._api_key},
            )
        except httpx.HTTPError as error:
            raise ProviderError("Hunter request failed") from error
        if response.status_code == 429:
            raise ProviderRateLimited("Hunter rate limit reached")
        if response.status_code >= 400:
            raise ProviderError(f"Hunter request failed with HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as error:
            raise ProviderError("Hunter returned an invalid response") from error
        data = payload.get("data") or {}
        organization = data.get("organization") or {}
        retrieved = datetime.now(UTC)
        source = f"hunter:domain:{normalized_domain}"
        mapping = {
            "company_description": organization.get("description"),
            "industry": organization.get("industry"),
            "country": organization.get("country"),
            "headquarters": organization.get("location"),
            "employee_range": organization.get("company_size"),
        }
        facts = tuple(
            ProviderFact(key, value, 80, None, retrieved, source)
            for key, value in mapping.items()
            if value not in (None, "", [])
        )
        meta = payload.get("meta") or {}
        return ProviderResult(
            provider_key=self.key,
            facts=facts,
            classification=DataClassification.THIRD_PARTY_LICENSED,
            redistribution_status=RedistributionStatus.UNKNOWN,
            request_identifier=response.headers.get("x-request-id"),
            credits_used=meta.get("params", {}).get("credits_used"),
            credits_remaining=meta.get("credits", {}).get("available"),
        )
