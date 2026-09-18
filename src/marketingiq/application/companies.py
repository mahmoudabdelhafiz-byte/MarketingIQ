from __future__ import annotations

import csv
import io
import ipaddress
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from marketingiq.application.authorization import Permission, require_permission
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.models import (
    AuditLog,
    Company,
    CompanyFact,
    CompanyIdentifier,
    DataClassification,
    DataSource,
    Evidence,
    OrganizationCompany,
    RedistributionStatus,
)

MAX_CSV_BYTES = 1_000_000
MAX_CSV_ROWS = 1_000
ALLOWED_SOURCE_KEYS = {"MANUAL", "CSV"}
CSV_ALIASES = {
    "company": "company_name",
    "name": "company_name",
    "website": "website_url",
    "country": "country_code",
    "employees_min": "employee_min",
    "employees_max": "employee_max",
}
CSV_FIELDS = {
    "company_name",
    "domain",
    "country_code",
    "industry",
    "employee_min",
    "employee_max",
    "website_url",
    "lifecycle_status",
    "private_notes",
}


def normalize_domain(value: str) -> str:
    raw = value.strip().lower()
    if not raw:
        raise ValueError("domain is required")
    parsed = urlsplit(raw if "://" in raw else f"//{raw}")
    if parsed.scheme and parsed.scheme not in {"http", "https"}:
        raise ValueError("domain scheme must be http or https")
    if parsed.username or parsed.password or parsed.port:
        raise ValueError("domain must not contain credentials or a port")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("domain must not contain a path, query, or fragment")
    host = (parsed.hostname or "").rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("IP addresses are not company domains")
    labels = host.split(".")
    if len(labels) < 2 or any(
        not label
        or len(label) > 63
        or label.startswith("-")
        or label.endswith("-")
        or not all(character.isalnum() or character == "-" for character in label)
        for label in labels
    ):
        raise ValueError("invalid company domain")
    return host


def validate_reference_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("reference_url must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ValueError("reference_url must not contain credentials")
    return value.strip()


@dataclass
class EvidenceInput:
    data_source_id: str | None = None
    reference_url: str | None = None
    reference_text: str | None = None
    retrieved_at: datetime | None = None
    last_verified_at: datetime | None = None


@dataclass
class FactInput:
    fact_key: str
    value: Any
    classification: DataClassification
    confidence: int
    redistribution_status: RedistributionStatus | None = None
    observed_at: datetime | None = None
    valid_until: datetime | None = None
    model_version: str | None = None
    research_run_id: str | None = None
    evidence: list[EvidenceInput] = field(default_factory=list)


@dataclass
class ImportRow:
    row_number: int
    company_name: str | None
    domain: str | None
    action: str
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    values: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class ImportReport:
    total_rows: int
    valid_rows: int
    invalid_rows: int
    new_companies: int
    existing_companies: int
    new_tenant_relationships: int
    already_attached: int
    warnings: list[str]
    rows: list[ImportRow]


class CompanyService:
    def __init__(self, session: Session, tenant: TenantContext) -> None:
        if not tenant.organization_id or not tenant.actor_user_id:
            raise ValueError("Tenant and actor are required")
        self.session = session
        self.tenant = tenant

    def _authorize(self, permission: Permission = Permission.READ) -> None:
        require_permission(self.tenant, permission)

    def _audit(self, action: str, entity_type: str, entity_id: str, metadata=None) -> None:
        self.session.add(
            AuditLog(
                organization_id=self.tenant.organization_id,
                actor_user_id=self.tenant.actor_user_id,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                metadata_json=metadata,
            )
        )

    def list_companies(self) -> list[OrganizationCompany]:
        self._authorize()
        return list(
            self.session.scalars(
                select(OrganizationCompany)
                .options(
                    selectinload(OrganizationCompany.company).selectinload(Company.identifiers)
                )
                .where(OrganizationCompany.organization_id == self.tenant.organization_id)
                .order_by(OrganizationCompany.created_at)
            )
        )

    def get_company(self, relationship_id: str) -> OrganizationCompany:
        self._authorize()
        relationship = self.session.scalar(
            select(OrganizationCompany)
            .options(selectinload(OrganizationCompany.company).selectinload(Company.identifiers))
            .where(
                OrganizationCompany.id == relationship_id,
                OrganizationCompany.organization_id == self.tenant.organization_id,
            )
        )
        if relationship is None:
            raise LookupError("organization company not found")
        return relationship

    def attach_company(
        self,
        *,
        domain: str,
        canonical_name: str,
        lifecycle_status: str | None = None,
        private_notes: str | None = None,
        shared_values: dict[str, Any] | None = None,
    ) -> OrganizationCompany:
        self._authorize(Permission.WRITE_CATALOG)
        normalized = normalize_domain(domain)
        company = self._company_by_domain(normalized)
        if company is None:
            values = {
                key: value for key, value in (shared_values or {}).items() if value is not None
            }
            company = Company(canonical_name=canonical_name.strip(), **values)
            company.identifiers.append(
                CompanyIdentifier(
                    kind="DOMAIN",
                    value=domain.strip(),
                    normalized_value=normalized,
                    is_primary=True,
                )
            )
            self.session.add(company)
            self.session.flush()
        relationship = self.session.scalar(
            select(OrganizationCompany).where(
                OrganizationCompany.organization_id == self.tenant.organization_id,
                OrganizationCompany.company_id == company.id,
            )
        )
        if relationship is None:
            relationship = OrganizationCompany(
                organization_id=self.tenant.organization_id,
                company=company,
                lifecycle_status=lifecycle_status,
                private_notes=private_notes,
            )
            self.session.add(relationship)
            self.session.flush()
            self._audit("company.attached", "organization_company", relationship.id)
        return relationship

    def update_relationship(
        self, relationship_id: str, *, lifecycle_status: str | None, private_notes: str | None
    ) -> OrganizationCompany:
        self._authorize(Permission.WRITE_CATALOG)
        relationship = self.get_company(relationship_id)
        relationship.lifecycle_status = lifecycle_status
        relationship.private_notes = private_notes
        self._audit("company.relationship_updated", "organization_company", relationship.id)
        return relationship

    def get_or_create_source(
        self, provider_key: str, display_name: str, external_reference: str | None = None
    ) -> DataSource:
        self._authorize(Permission.WRITE_CATALOG)
        key = provider_key.strip().upper()
        if key not in ALLOWED_SOURCE_KEYS:
            raise ValueError("Only MANUAL and CSV data sources may be created in Sprint 1")
        source = self.session.scalar(
            select(DataSource).where(
                DataSource.provider_key == key,
                DataSource.external_reference == external_reference,
            )
        )
        if source is None:
            source = DataSource(
                provider_key=key,
                display_name=display_name.strip(),
                external_reference=external_reference,
            )
            self.session.add(source)
            self.session.flush()
        return source

    def list_sources(self) -> list[DataSource]:
        self._authorize()
        return list(self.session.scalars(select(DataSource).order_by(DataSource.display_name)))

    def list_facts(self, relationship_id: str) -> list[CompanyFact]:
        relationship = self.get_company(relationship_id)
        return list(
            self.session.scalars(
                select(CompanyFact)
                .options(selectinload(CompanyFact.evidences).selectinload(Evidence.source))
                .where(
                    CompanyFact.company_id == relationship.company_id,
                    or_(
                        CompanyFact.organization_id.is_(None),
                        CompanyFact.organization_id == self.tenant.organization_id,
                    ),
                )
                .order_by(CompanyFact.observed_at, CompanyFact.id)
            )
        )

    def add_fact(self, relationship_id: str, data: FactInput) -> CompanyFact:
        self._authorize(Permission.WRITE_CATALOG)
        relationship = self.get_company(relationship_id)
        if not data.fact_key.strip() or not 0 <= data.confidence <= 100:
            raise ValueError("fact_key and confidence from 0 through 100 are required")
        status = data.redistribution_status or RedistributionStatus.UNKNOWN
        if (
            data.classification == DataClassification.THIRD_PARTY_LICENSED
            and status == RedistributionStatus.ALLOWED
        ):
            raise ValueError("third-party licensed facts cannot default to ALLOWED")
        if data.classification in {
            DataClassification.PUBLIC_EVIDENCE,
            DataClassification.THIRD_PARTY_LICENSED,
        } and not any(item.data_source_id for item in data.evidence):
            raise ValueError("public and third-party facts require data-source provenance")
        fact = CompanyFact(
            company_id=relationship.company_id,
            organization_id=self.tenant.organization_id,
            fact_key=data.fact_key.strip(),
            value=data.value,
            classification=data.classification,
            redistribution_status=status,
            confidence=data.confidence,
            observed_at=data.observed_at,
            valid_until=data.valid_until,
            model_version=data.model_version,
            research_run_id=data.research_run_id,
        )
        for item in data.evidence:
            if (
                item.data_source_id is not None
                and self.session.get(DataSource, item.data_source_id) is None
            ):
                raise ValueError("unknown data source")
            fact.evidences.append(
                Evidence(
                    data_source_id=item.data_source_id,
                    reference_url=(
                        validate_reference_url(item.reference_url) if item.reference_url else None
                    ),
                    reference_text=item.reference_text,
                    retrieved_at=item.retrieved_at,
                    last_verified_at=item.last_verified_at,
                )
            )
        self.session.add(fact)
        self.session.flush()
        self._audit("company.fact_added", "company_fact", fact.id)
        for evidence in fact.evidences:
            self._audit("company.evidence_added", "evidence", evidence.id, {"fact_id": fact.id})
        return fact

    def preview_csv(self, content: str | bytes) -> ImportReport:
        self._authorize(Permission.IMPORT_COMPANIES)
        text = self._decode_csv(content)
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames is None:
            raise ValueError("CSV header is required")
        headers = [
            CSV_ALIASES.get((item or "").strip().lower(), (item or "").strip().lower())
            for item in reader.fieldnames
        ]
        if len(set(headers)) != len(headers):
            raise ValueError("CSV contains duplicate or conflicting headers")
        if "domain" not in headers or "company_name" not in headers:
            raise ValueError("CSV requires company_name and domain headers")
        unknown = set(headers) - CSV_FIELDS
        if unknown:
            raise ValueError(f"unsupported CSV headers: {', '.join(sorted(unknown))}")
        raw_rows = list(reader)
        if len(raw_rows) > MAX_CSV_ROWS:
            raise ValueError(f"CSV exceeds the {MAX_CSV_ROWS} row limit")
        existing = {
            identifier.normalized_value: identifier.company_id
            for identifier in self.session.scalars(
                select(CompanyIdentifier).where(CompanyIdentifier.kind == "DOMAIN")
            )
        }
        attached = set(
            self.session.scalars(
                select(OrganizationCompany.company_id).where(
                    OrganizationCompany.organization_id == self.tenant.organization_id
                )
            )
        )
        virtual_new: dict[str, str] = {}
        virtual_attached = set(attached)
        rows: list[ImportRow] = []
        for number, raw in enumerate(raw_rows, 2):
            values = {
                headers[index]: (raw.get(original) or "").strip()
                for index, original in enumerate(reader.fieldnames)
            }
            rows.append(self._preview_row(number, values, existing, virtual_new, virtual_attached))
        valid = [row for row in rows if not row.errors]
        return ImportReport(
            total_rows=len(rows),
            valid_rows=len(valid),
            invalid_rows=len(rows) - len(valid),
            new_companies=sum(row.action == "CREATE_GLOBAL_AND_ATTACH" for row in rows),
            existing_companies=sum(row.action == "REUSE_GLOBAL_AND_ATTACH" for row in rows),
            new_tenant_relationships=sum(
                row.action in {"CREATE_GLOBAL_AND_ATTACH", "REUSE_GLOBAL_AND_ATTACH"}
                for row in rows
            ),
            already_attached=sum(row.action == "ALREADY_ATTACHED" for row in rows),
            warnings=[warning for row in rows for warning in row.warnings],
            rows=rows,
        )

    def import_csv(self, content: str | bytes) -> ImportReport:
        report = self.preview_csv(content)
        if report.invalid_rows:
            raise ValueError("CSV has invalid rows; import was not executed")
        self.get_or_create_source("CSV", "CSV company import")
        for row in report.rows:
            if row.action == "ALREADY_ATTACHED":
                continue
            values = row.values
            shared = {
                "website_url": values.get("website_url") or None,
                "country_code": values.get("country_code") or None,
                "industry": values.get("industry") or None,
                "employee_min": values.get("employee_min"),
                "employee_max": values.get("employee_max"),
            }
            relationship = self.attach_company(
                domain=values["domain"],
                canonical_name=values["company_name"],
                lifecycle_status=values.get("lifecycle_status") or None,
                private_notes=values.get("private_notes") or None,
                shared_values=shared,
            )
            # Existing non-null shared values are never replaced; only missing fields are filled.
            for key, value in shared.items():
                if value is not None and getattr(relationship.company, key) is None:
                    setattr(relationship.company, key, value)
        self._audit(
            "company.csv_imported",
            "organization",
            self.tenant.organization_id,
            {"total_rows": report.total_rows, "new_companies": report.new_companies},
        )
        return report

    def _decode_csv(self, content: str | bytes) -> str:
        if isinstance(content, bytes):
            if len(content) > MAX_CSV_BYTES:
                raise ValueError(f"CSV exceeds the {MAX_CSV_BYTES} byte limit")
            try:
                return content.decode("utf-8-sig")
            except UnicodeDecodeError as error:
                raise ValueError("CSV must be UTF-8 encoded") from error
        if len(content.encode("utf-8")) > MAX_CSV_BYTES:
            raise ValueError(f"CSV exceeds the {MAX_CSV_BYTES} byte limit")
        return content.lstrip("\ufeff")

    def _company_by_domain(self, domain: str) -> Company | None:
        return self.session.scalar(
            select(Company)
            .join(CompanyIdentifier)
            .where(
                CompanyIdentifier.kind == "DOMAIN",
                CompanyIdentifier.normalized_value == domain,
            )
        )

    def _preview_row(self, number, values, existing, virtual_new, virtual_attached) -> ImportRow:
        errors: list[str] = []
        warnings: list[str] = []
        name = values.get("company_name", "").strip()
        raw_domain = values.get("domain", "").strip()
        if not name:
            errors.append("company_name is required")
        try:
            domain = normalize_domain(raw_domain)
        except ValueError as error:
            domain = raw_domain or None
            errors.append(str(error))
        for key in ("employee_min", "employee_max"):
            raw = values.get(key, "")
            if raw:
                try:
                    values[key] = int(raw)
                    if values[key] < 0:
                        raise ValueError
                except ValueError:
                    errors.append(f"{key} must be a non-negative integer")
            else:
                values[key] = None
        if (
            values.get("employee_min") is not None
            and values.get("employee_max") is not None
            and values["employee_min"] > values["employee_max"]
        ):
            errors.append("employee_min must not exceed employee_max")
        country = values.get("country_code", "")
        if country and (len(country) != 2 or not country.isalpha()):
            errors.append("country_code must contain two letters")
        values["country_code"] = country.upper() or ""
        if values.get("website_url"):
            try:
                validate_reference_url(values["website_url"])
            except ValueError:
                errors.append("website_url must be an absolute HTTP(S) URL")
        if errors:
            action = "REJECT"
        else:
            values["domain"] = domain
            company_id = existing.get(domain) or virtual_new.get(domain)
            if company_id in virtual_attached:
                action = "ALREADY_ATTACHED"
                warnings.append(
                    "duplicate or already attached domain; row values will not overwrite"
                )
            elif company_id:
                action = "REUSE_GLOBAL_AND_ATTACH"
                virtual_attached.add(company_id)
            else:
                action = "CREATE_GLOBAL_AND_ATTACH"
                placeholder = f"new:{domain}"
                virtual_new[domain] = placeholder
                virtual_attached.add(placeholder)
        return ImportRow(number, name or None, domain, action, warnings, errors, values)
