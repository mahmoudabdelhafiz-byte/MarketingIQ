"""People from the organization's own lists, waiting to join decision-maker discovery."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from marketingiq.domain.models import Base, new_id, utcnow


class ImportedContactStatus(enum.StrEnum):
    PENDING = "PENDING"  # company not qualified yet; joins the next decision-maker run
    ATTACHED = "ATTACHED"  # now a contact candidate
    SUPPRESSED = "SUPPRESSED"  # address is on the suppression list; never attached


class ImportedContact(Base):
    __tablename__ = "imported_contacts"
    __table_args__ = (
        UniqueConstraint("organization_id", "dedupe_key", name="uq_imported_contact_key"),
        Index("ix_imported_contact_rel_status", "organization_company_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    organization_company_id: Mapped[str] = mapped_column(
        ForeignKey("organization_companies.id"), index=True
    )
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    dedupe_key: Mapped[str] = mapped_column(String(320))
    list_name: Mapped[str] = mapped_column(String(120))
    first_name: Mapped[str | None] = mapped_column(String(100))
    last_name: Mapped[str | None] = mapped_column(String(100))
    full_name: Mapped[str | None] = mapped_column(String(220))
    job_title: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320))
    linkedin_url: Mapped[str | None] = mapped_column(String(500))
    trusted: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[ImportedContactStatus] = mapped_column(
        Enum(
            ImportedContactStatus,
            name="imported_contact_status",
            native_enum=False,
            validate_strings=True,
            create_constraint=True,
        ),
        default=ImportedContactStatus.PENDING,
    )
    contact_id: Mapped[str | None] = mapped_column(ForeignKey("contact_candidates.id"))
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    attached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
