"""Import people from your own lists (CSV)."""

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from marketingiq.application.contact_imports import ContactImportService
from marketingiq.application.tenant import TenantContext


class ContactCsv(BaseModel):
    content: str = Field(min_length=1, max_length=2_000_000)


class ContactImportRequest(ContactCsv):
    list_name: str = Field(min_length=1, max_length=120)
    trusted: bool = False


def register_contact_import_routes(
    app: FastAPI,
    prefix: str,
    tenant_dependency: Callable[..., TenantContext],
    session_dependency: Callable[..., Session],
) -> None:
    def import_service(
        context: Annotated[TenantContext, Depends(tenant_dependency)],
        db: Annotated[Session, Depends(session_dependency)],
    ) -> ContactImportService:
        return ContactImportService(db, context)

    Imports = Annotated[ContactImportService, Depends(import_service)]

    @app.get(prefix + "/contact-imports")
    def contact_lists(svc: Imports):
        return svc.lists()

    @app.post(prefix + "/contact-imports/preview")
    def preview_contact_import(body: ContactCsv, svc: Imports):
        return svc.preview(body.content)

    @app.post(prefix + "/contact-imports", status_code=201)
    def contact_import(body: ContactImportRequest, svc: Imports):
        return svc.import_csv(body.content, list_name=body.list_name, trusted=body.trusted)
