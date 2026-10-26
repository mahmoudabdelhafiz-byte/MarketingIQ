"""Find people named on a company's own leadership/team pages.

Only same-domain pages are fetched, through the SSRF-safe public-web fetcher, with a small page
cap. LinkedIn profile links that the company itself publishes next to a person are recorded as
references; LinkedIn is never requested, crawled or logged into.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from marketingiq.application.decision_maker_scoring import normalize_linkedin
from marketingiq.application.role_resolver import DECISION_TITLE_WORDS, normalize_text
from marketingiq.domain.providers import ProviderError

LEADERSHIP_HINT = re.compile(
    r"leadership|management|our-?team|the-?team|\bteam\b|board|executive|directors|founders|"
    r"who-?we-?are|about|people|الإدارة|فريق",
    re.I,
)
STRONG_HINT = re.compile(r"leadership|management|team|board|executive|directors|founders", re.I)
FALLBACK_PATHS = ("/about", "/about-us", "/leadership", "/our-team", "/team", "/management")
SKIP_TAGS = frozenset({"script", "style", "noscript", "svg", "template", "iframe"})
BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "li",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "td",
        "th",
        "br",
        "tr",
        "section",
        "article",
        "figcaption",
        "dt",
        "dd",
        "header",
        "footer",
        "ul",
        "ol",
    }
)
HONORIFICS = re.compile(r"^(dr|eng|mr|mrs|ms|miss|prof|sheikh|sir|h\.?e)\.?\s+", re.I)
NAME_TOKEN = re.compile(r"^[A-Z][A-Za-z'’\-]*\.?$")
NOT_NAME_WORDS = frozenset(
    {
        "about",
        "our",
        "team",
        "leadership",
        "management",
        "board",
        "contact",
        "us",
        "home",
        "services",
        "service",
        "news",
        "read",
        "more",
        "meet",
        "the",
        "company",
        "group",
        "careers",
        "directors",
        "director",
        "executive",
        "message",
        "solutions",
        "products",
        "view",
        "profile",
        "linkedin",
        "email",
        "learn",
        "privacy",
        "policy",
        "terms",
        "all",
        "rights",
        "reserved",
        "copyright",
        "vision",
        "mission",
        "values",
        "clients",
        "partners",
        "chief",
        "officer",
        "manager",
        "head",
        "president",
        "founder",
        "chairman",
        "vice",
        "general",
        "managing",
        "senior",
        "and",
        "of",
        "for",
        "with",
        "welcome",
        "follow",
        "subscribe",
        "industries",
        "insights",
        "blog",
        "events",
        "why",
        "who",
        "we",
        "are",
        "technology",
        "digital",
        "human",
        "resources",
        "marketing",
        "sales",
        "operations",
        "information",
        "customer",
        "support",
        "get",
        "in",
        "touch",
        "saudi",
        "arabia",
        "uae",
        "dubai",
        "riyadh",
        "egypt",
        "cairo",
        "limited",
        "llc",
        "ltd",
    }
)
SPLITTERS = re.compile(r"\s+[\-–—|:]\s+|,\s+")
TITLE_EXTRA = frozenset({"ceo", "cio", "cto", "chro", "cfo", "coo", "cmo", "md", "gm", "vp"})


@dataclass(frozen=True)
class PagePerson:
    name: str
    title: str
    snippet: str
    linkedin_url: str | None = None


@dataclass
class PageObservation:
    url: str
    retrieved_at: datetime
    text: str
    people: list[PagePerson] = field(default_factory=list)


@dataclass
class ScanResult:
    pages: list[PageObservation] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    fetched: int = 0


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.nodes: list[str] = []
        self.links: list[tuple[str, str, int]] = []  # href, text, node index at start
        self._skip = 0
        self._buffer: list[str] = []
        self._anchor: tuple[str, int] | None = None
        self._anchor_text: list[str] = []

    def _flush(self) -> None:
        text = re.sub(r"\s+", " ", "".join(self._buffer)).strip()
        self._buffer = []
        if text:
            self.nodes.append(text)

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self._skip += 1
            return
        if tag in BLOCK_TAGS:
            self._flush()
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self._anchor = (href, len(self.nodes))
                self._anchor_text = []

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if tag in BLOCK_TAGS:
            self._flush()
        if tag == "a" and self._anchor:
            href, index = self._anchor
            self.links.append((href, " ".join(self._anchor_text).strip(), index))
            self._anchor = None

    def handle_data(self, data):
        if self._skip:
            return
        self._buffer.append(data)
        if self._anchor:
            self._anchor_text.append(data)

    def close(self):
        super().close()
        self._flush()


def _clean_name(text: str) -> str | None:
    value = HONORIFICS.sub("", text.strip()).strip(" ,.;:")
    tokens = value.split()
    if not 2 <= len(tokens) <= 4 or len(value) > 60:
        return None
    for token in tokens:
        if not NAME_TOKEN.match(token) and not NAME_TOKEN.match(token.title()):
            return None
        if token.lower().strip(".") in NOT_NAME_WORDS:
            return None
    if sum(1 for t in tokens if len(t.strip(".")) > 1) < 2:
        return None
    return " ".join(tokens)


def looks_like_title(text: str) -> bool:
    if not 2 <= len(text) <= 120 or len(text.split()) > 14:
        return False
    raw = set(re.findall(r"[a-z]+", text.lower()))
    return bool(raw & TITLE_EXTRA) or bool(set(normalize_text(text).split()) & DECISION_TITLE_WORDS)


def extract_people(nodes: list[str], links: list[tuple[str, str, int]]) -> list[PagePerson]:
    found: list[tuple[int, str, str, str]] = []
    for index, node in enumerate(nodes):
        name = _clean_name(node)
        if name and index + 1 < len(nodes) and looks_like_title(nodes[index + 1]):
            title = nodes[index + 1]
            if not _clean_name(title):
                found.append((index, name, title, f"{node} — {title}"))
                continue
        parts = SPLITTERS.split(node, maxsplit=1)
        if len(parts) == 2:
            name = _clean_name(parts[0])
            if name and looks_like_title(parts[1]) and not _clean_name(parts[1]):
                found.append((index, name, parts[1].strip(), node))
    people: list[PagePerson] = []
    seen: set[str] = set()
    for position, (index, name, title, snippet) in enumerate(found):
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        # Only links between this person and the next one, and only if exactly one profile.
        limit = found[position + 1][0] if position + 1 < len(found) else index + 6
        candidates = {
            normalize_linkedin(href)
            for href, _text, link_index in links
            if index <= link_index < min(limit, index + 6)
        } - {None}
        linkedin = candidates.pop() if len(candidates) == 1 else None
        people.append(PagePerson(name, title[:255], snippet[:500], linkedin))
    return people[:60]


def _same_site(url: str, domain: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host == domain or host == f"www.{domain}"


class LeadershipPageScanner:
    def __init__(self, public_provider, max_pages: int = 5) -> None:
        self.public = public_provider
        self.max_pages = max_pages
        self._hosts: frozenset[str] | None = None

    def _get(self, url: str, result: ScanResult) -> tuple[str, str, datetime] | None:
        if result.fetched >= self.max_pages:
            return None
        result.fetched += 1
        try:
            return self.public._fetch(url, allowed_hosts=self._hosts)
        except ProviderError as error:
            result.errors.append(getattr(error, "category", "PROVIDER_ERROR"))
            return None

    @staticmethod
    def _observe(url: str, html: str, retrieved: datetime) -> tuple[PageObservation, list]:
        collector = _Collector()
        collector.feed(html)
        collector.close()
        people = extract_people(collector.nodes, collector.links)
        text = "\n".join(collector.nodes)
        return PageObservation(url, retrieved, text, people), collector.links

    def scan(self, domain: str) -> ScanResult:
        result = ScanResult()
        domain = domain.lower()
        # Never follow a redirect off the company's own site (e.g. to a social network).
        self._hosts = frozenset({domain, f"www.{domain}"})
        home = self._get(f"https://{domain}/", result)
        candidates: list[tuple[int, str]] = []
        if home and not _same_site(home[0], domain):
            home = None
        if home:
            home_url, html, retrieved = home
            observation, links = self._observe(home_url, html, retrieved)
            if observation.people:
                result.pages.append(observation)
            for href, text, _ in links:
                url = urljoin(home_url, href).split("#", 1)[0]
                if not _same_site(url, domain) or url.rstrip("/") == home_url.rstrip("/"):
                    continue
                path = urlsplit(url).path
                label = f"{text} {path}"
                if LEADERSHIP_HINT.search(label):
                    score = 3 if STRONG_HINT.search(label) else 1
                    if url not in [u for _, u in candidates]:
                        candidates.append((score, url))
        if not candidates:
            candidates = [(1, f"https://{domain}{path}") for path in FALLBACK_PATHS]
        candidates.sort(key=lambda item: (-item[0], len(item[1])))
        for _, url in candidates:
            if result.fetched >= self.max_pages:
                break
            fetched = self._get(url, result)
            if fetched:
                page_url, html, retrieved = fetched
                if not _same_site(page_url, domain):
                    continue
                observation, _ = self._observe(page_url, html, retrieved)
                result.pages.append(observation)
        return result
