"""Import people from your own lists (CSV): company, name, title, email, LinkedIn.

Each person's company is added (or matched by domain), and the person waits as PENDING until the
company is qualified for a product; the next decision-maker run then adds them with the same role,
evidence and email checks as discovered people. A list marked as trusted is pre-approved.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from marketingiq.application.authorization import Permission, require_permission
from marketingiq.application.companies import CompanyService, normalize_domain
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.contact_imports import ImportedContact, ImportedContactStatus
from marketingiq.domain.models import AuditLog
from marketingiq.domain.outbound import SuppressionEntry

MAX_BYTES = 2_000_000
MAX_ROWS = 2_000
FREE_MAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "hotmail.com", "outlook.com", "live.com",
    "icloud.com", "me.com", "aol.com", "proton.me", "protonmail.com", "gmx.com", "mail.com",
    "yandex.com", "zoho.com", "msn.com",
}  # fmt: skip
ALIASES = {
    "company": "company",
    "company_name": "company",
    "organization": "company",
    "organisation": "company",
    "account": "company",
    "domain": "domain",
    "company_domain": "domain",
    "website": "domain",
    "company_website": "domain",
    "first_name": "first_name",
    "firstname": "first_name",
    "given_name": "first_name",
    "last_name": "last_name",
    "lastname": "last_name",
    "surname": "last_name",
    "family_name": "last_name",
    "name": "full_name",
    "full_name": "full_name",
    "title": "title",
    "job_title": "title",
    "position": "title",
    "role": "title",
    "email": "email",
    "email_address": "email",
    "work_email": "email",
    "business_email": "email",
    "linkedin": "linkedin",
    "linkedin_url": "linkedin",
    "linkedin_profile": "linkedin",
    "person_linkedin_url": "linkedin",
}
_EMAIL_RE = re.compile(r"^[^@\s]+@([^@\s]+\.[^@\s]+)$")


@dataclass
class ImportRow:
    number: int
    company: str | None = None
    domain: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    full_name: str | None = None
    title: str | None = None
    email: str | None = None
    linkedin: str | None = None
    error: str | None = None

    @property
    def dedupe_key(self) -> str:
        if self.email:
            return self.email
        name = re.sub(r"\s+", " ", (self.full_name or "").strip().lower())
        return f"{self.domain}|{name}"[:320]


@dataclass
class ImportResult:
    total_rows: int = 0
    valid_rows: int = 0
    imported: int = 0
    duplicates: int = 0
    suppressed: int = 0
    new_companies: int = 0
    invalid: list[dict[str, Any]] = field(default_factory=list)
    relationship_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_rows": self.total_rows,
            "valid_rows": self.valid_rows,
            "imported": self.imported,
            "duplicates": self.duplicates,
            "suppressed": self.suppressed,
            "new_companies": self.new_companies,
            "invalid": self.invalid[:100],
            "invalid_count": len(self.invalid),
            "relationship_ids": self.relationship_ids,
        }


def _clean(value: Any, limit: int) -> str | None:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit] or None


def _header(name: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")
    return ALIASES.get(key, key)


def parse_rows(content: str | bytes) -> list[ImportRow]:
    if isinstance(content, bytes):
        if len(content) > MAX_BYTES:
            raise ValueError(f"The file is larger than {MAX_BYTES // 1_000_000} MB")
        try:
            content = content.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise ValueError("The file must be UTF-8 encoded CSV") from error
    elif len(content.encode("utf-8")) > MAX_BYTES:
        raise ValueError(f"The file is larger than {MAX_BYTES // 1_000_000} MB")
    text = content.lstrip("﻿")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise ValueError("The file has no header row")
    headers = {name: _header(name) for name in reader.fieldnames if name}
    if "email" not in headers.values() and not (
        {"full_name", "first_name"} & set(headers.values())
    ):
        raise ValueError("The file needs an email column or a name column")
    rows: list[ImportRow] = []
    for number, raw in enumerate(reader, start=2):
        if len(rows) >= MAX_ROWS:
            raise ValueError(f"The file has more than {MAX_ROWS} rows")
        values = {headers[k]: v for k, v in raw.items() if k in headers and v}
        if not any(str(v).strip() for v in values.values()):
            continue
        rows.append(_row(number, values))
    return rows


def _row(number: int, values: dict[str, Any]) -> ImportRow:
    row = ImportRow(
        number=number,
        company=_clean(values.get("company"), 200),
        first_name=_clean(values.get("first_name"), 100),
        last_name=_clean(values.get("last_name"), 100),
        full_name=_clean(values.get("full_name"), 220),
        title=_clean(values.get("title"), 200),
        linkedin=_clean(values.get("linkedin"), 500),
    )
    email = (_clean(values.get("email"), 320) or "").lower() or None
    email_domain = None
    if email:
        match = _EMAIL_RE.match(email)
        if not match:
            row.error = "Email address is not valid"
            return row
        row.email = email
        email_domain = match.group(1)
    if not row.full_name and (row.first_name or row.last_name):
        row.full_name = " ".join(p for p in (row.first_name, row.last_name) if p)[:220]
    if row.full_name and not row.first_name:
        parts = row.full_name.split(" ")
        row.first_name = parts[0][:100]
        row.last_name = row.last_name or (" ".join(parts[1:])[:100] or None)
    if not row.email and not row.full_name:
        row.error = "A name or an email is required"
        return row
    raw_domain = _clean(values.get("domain"), 255)
    domain = None
    if raw_domain:
        # Websites often come with a path ("https://acme.com/about"): keep the host only.
        host = re.sub(r"^(?:[a-z]+://)?([^/?#\s]+).*$", r"\1", raw_domain, flags=re.IGNORECASE)
        try:
            domain = normalize_domain(host)
        except ValueError:
            domain = None
    if not domain and email_domain and email_domain not in FREE_MAIL_DOMAINS:
        domain = email_domain
    if not domain:
        row.error = "A company domain or website is required (or a business email)"
        return row
    if email_domain in FREE_MAIL_DOMAINS:
        row.error = "Personal email addresses (Gmail, Outlook…) cannot be used for outreach"
        return row
    row.domain = domain
    row.company = row.company or domain.split(".")[0].replace("-", " ").title()
    return row


class ContactImportService:
    def __init__(self, session: Session, tenant: TenantContext, now: datetime | None = None):
        self.session = session
        self.tenant = tenant
        self.now = now or datetime.now(UTC)

    def preview(self, content: str | bytes) -> dict[str, Any]:
        require_permission(self.tenant, Permission.IMPORT_COMPANIES)
        return self._run(content, list_name="preview", trusted=False, execute=False).as_dict()

    def import_csv(
        self, content: str | bytes, *, list_name: str, trusted: bool = False
    ) -> dict[str, Any]:
        require_permission(self.tenant, Permission.IMPORT_COMPANIES)
        require_permission(self.tenant, Permission.WRITE_CATALOG)
        if trusted:
            require_permission(self.tenant, Permission.RUN_QUALIFICATION)
        clean_name = _clean(list_name, 120)
        if not clean_name:
            raise ValueError("Give the list a name")
        result = self._run(content, list_name=clean_name, trusted=trusted, execute=True)
        self.session.add(
            AuditLog(
                organization_id=self.tenant.organization_id,
                actor_user_id=self.tenant.actor_user_id,
                action="contacts.list_imported",
                entity_type="organization",
                entity_id=self.tenant.organization_id,
                metadata_json={
                    "list_name": clean_name,
                    "trusted": trusted,
                    "imported": result.imported,
                    "duplicates": result.duplicates,
                    "invalid": len(result.invalid),
                    "new_companies": result.new_companies,
                },
            )
        )
        self.session.flush()
        return result.as_dict()

    def lists(self) -> list[dict[str, Any]]:
        require_permission(self.tenant, Permission.READ)
        rows = self.session.execute(
            select(
                ImportedContact.list_name,
                ImportedContact.status,
                func.count(ImportedContact.id),
                func.max(ImportedContact.created_at),
            )
            .where(ImportedContact.organization_id == self.tenant.organization_id)
            .group_by(ImportedContact.list_name, ImportedContact.status)
        ).all()
        lists: dict[str, dict[str, Any]] = {}
        for name, status, count, last in rows:
            item = lists.setdefault(
                name, {"list_name": name, "total": 0, "pending": 0, "attached": 0,
                       "suppressed": 0, "last_imported_at": last}  # fmt: skip
            )
            item["total"] += int(count)
            item[ImportedContactStatus(status).value.lower()] += int(count)
            if last and (item["last_imported_at"] is None or last > item["last_imported_at"]):
                item["last_imported_at"] = last
        return sorted(lists.values(), key=lambda x: str(x["last_imported_at"]), reverse=True)

    # -----------------------------------------------------------------------------------------

    def _run(self, content, *, list_name: str, trusted: bool, execute: bool) -> ImportResult:
        rows = parse_rows(content)
        result = ImportResult(total_rows=len(rows))
        companies = CompanyService(self.session, self.tenant)
        seen: set[str] = set()
        known_domains: set[str] = set()
        relationships: dict[str, Any] = {}
        for row in rows:
            if row.error:
                result.invalid.append({"row": row.number, "error": row.error})
                continue
            result.valid_rows += 1
            key = row.dedupe_key
            if key in seen or self._exists(key):
                result.duplicates += 1
                continue
            seen.add(key)
            if row.domain not in known_domains:
                known_domains.add(row.domain)
                if companies._company_by_domain(row.domain) is None:
                    result.new_companies += 1
            if not execute:
                result.imported += 1
                continue
            relationship = relationships.get(row.domain)
            if relationship is None:
                relationship = companies.attach_company(
                    domain=row.domain, canonical_name=row.company or row.domain
                )
                relationships[row.domain] = relationship
            suppressed = bool(row.email) and self._suppressed(row.email)
            self.session.add(
                ImportedContact(
                    organization_id=self.tenant.organization_id,
                    organization_company_id=relationship.id,
                    company_id=relationship.company_id,
                    dedupe_key=key,
                    list_name=list_name,
                    first_name=row.first_name,
                    last_name=row.last_name,
                    full_name=row.full_name,
                    job_title=row.title,
                    email=row.email,
                    linkedin_url=row.linkedin,
                    trusted=trusted,
                    status=ImportedContactStatus.SUPPRESSED
                    if suppressed
                    else ImportedContactStatus.PENDING,
                    created_by_user_id=self.tenant.actor_user_id,
                    created_at=self.now,
                )
            )
            if suppressed:
                result.suppressed += 1
            else:
                result.imported += 1
        if execute:
            self.session.flush()
            result.relationship_ids = [rel.id for rel in relationships.values()]
        return result

    def _exists(self, key: str) -> bool:
        return (
            self.session.scalar(
                select(ImportedContact.id).where(
                    ImportedContact.organization_id == self.tenant.organization_id,
                    ImportedContact.dedupe_key == key,
                )
            )
            is not None
        )

    def _suppressed(self, email: str) -> bool:
        return (
            self.session.scalar(
                select(SuppressionEntry.id).where(
                    SuppressionEntry.organization_id == self.tenant.organization_id,
                    SuppressionEntry.email_normalized == email.strip().lower(),
                )
            )
            is not None
        )


def pending_imported_count(session: Session, relationship_id: str) -> int:
    return int(
        session.scalar(
            select(func.count(ImportedContact.id)).where(
                ImportedContact.organization_company_id == relationship_id,
                ImportedContact.status == ImportedContactStatus.PENDING,
            )
        )
        or 0
    )
