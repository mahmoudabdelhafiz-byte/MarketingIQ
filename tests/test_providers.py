import httpx
import pytest

from marketingiq.domain.models import DataClassification, RedistributionStatus
from marketingiq.domain.providers import ProviderNotConfigured, ProviderRateLimited
from marketingiq.infrastructure.providers import (
    HunterProvider,
    PublicWebProvider,
    UnsafePublicUrl,
    validate_public_url,
)


def public_dns(_host):
    return {"93.184.216.34"}


@pytest.mark.parametrize(
    "url,address",
    [
        ("http://localhost/", "127.0.0.1"),
        ("https://company.test/", "10.0.0.2"),
        ("https://company.test/", "169.254.1.2"),
        ("file:///etc/passwd", "93.184.216.34"),
        ("https://user:secret@company.test/", "93.184.216.34"),
    ],
)
def test_public_url_rejects_ssrf_targets(url, address):
    with pytest.raises(UnsafePublicUrl):
        validate_public_url(url, lambda _host: {address})


def test_public_provider_maps_supported_pages_without_crawling():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if request.url.path == "/":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text=(
                    '<title>Acme</title><meta name="description" content="Makes widgets">'
                    '<a href="/about">About us</a><a href="/blog">Blog</a>'
                    '<a href="/careers">Careers</a>'
                ),
            )
        return httpx.Response(
            200, headers={"content-type": "text/html"}, text="<title>Page</title>"
        )

    provider = PublicWebProvider(
        httpx.Client(transport=httpx.MockTransport(handler)), resolver=public_dns
    )
    result = provider.enrich_company("acme.test")
    assert [fact.key for fact in result.facts] == [
        "company_description",
        "website_title",
        "active_hiring",
    ]
    assert all(fact.value is not False for fact in result.facts)
    assert not any("blog" in call for call in calls)
    assert result.classification == DataClassification.PUBLIC_EVIDENCE


def test_public_provider_validates_redirect_target():
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(302, headers={"location": "http://127.0.0.1/admin"})
        )
    )
    provider = PublicWebProvider(client, resolver=lambda host: {host})
    with pytest.raises(UnsafePublicUrl):
        provider.enrich_company("93.184.216.34")


def test_public_provider_rejects_oversized_and_non_html_responses():
    for response in (
        httpx.Response(200, headers={"content-type": "text/html"}, content=b"12345"),
        httpx.Response(200, headers={"content-type": "application/json"}, content=b"{}"),
    ):
        provider = PublicWebProvider(
            httpx.Client(transport=httpx.MockTransport(lambda _request, r=response: r)),
            resolver=public_dns,
            max_bytes=4,
        )
        with pytest.raises(RuntimeError):
            provider.enrich_company("acme.test")


def test_hunter_missing_key_is_safe_and_not_configured(monkeypatch):
    monkeypatch.delenv("HUNTER_API_KEY", raising=False)
    provider = HunterProvider()
    assert not provider.configured
    with pytest.raises(ProviderNotConfigured):
        provider.enrich_company("acme.test")


def test_hunter_maps_company_fields_and_never_returns_secret():
    secret = "top-secret-key"

    def handler(request):
        assert request.url.params["api_key"] == secret
        return httpx.Response(
            200,
            headers={"x-request-id": "safe-123"},
            json={
                "data": {
                    "organization": {
                        "description": "Widgets",
                        "industry": "Manufacturing",
                        "location": "Cairo",
                        "company_size": "51-200",
                    }
                }
            },
        )

    provider = HunterProvider(secret, httpx.Client(transport=httpx.MockTransport(handler)))
    result = provider.enrich_company("acme.test")
    assert result.classification == DataClassification.THIRD_PARTY_LICENSED
    assert result.redistribution_status == RedistributionStatus.UNKNOWN
    assert {fact.key for fact in result.facts} == {
        "company_description",
        "industry",
        "headquarters",
        "employee_range",
    }
    assert secret not in repr(result)


def test_hunter_rate_limit_is_categorized():
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(429)))
    with pytest.raises(ProviderRateLimited):
        HunterProvider("secret", client).enrich_company("acme.test")
