from enum import StrEnum

from marketingiq.application.errors import AuthorizationError
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.models import MembershipRole


class Permission(StrEnum):
    READ = "READ"
    WRITE_CATALOG = "WRITE_CATALOG"
    IMPORT_COMPANIES = "IMPORT_COMPANIES"
    RUN_PUBLIC_RESEARCH = "RUN_PUBLIC_RESEARCH"
    RUN_EXTERNAL_RESEARCH = "RUN_EXTERNAL_RESEARCH"
    REVIEW_INTELLIGENCE = "REVIEW_INTELLIGENCE"
    RUN_FIT_ASSESSMENT = "RUN_FIT_ASSESSMENT"
    RUN_QUALIFICATION = "RUN_QUALIFICATION"
    MANAGE_BUYER_ROLES = "MANAGE_BUYER_ROLES"
    SPEND_PROVIDER_CREDITS = "SPEND_PROVIDER_CREDITS"
    GENERATE_CAMPAIGN_DRAFT = "GENERATE_CAMPAIGN_DRAFT"


ROLE_PERMISSIONS = {
    MembershipRole.ORGANIZATION_ADMIN: frozenset(Permission),
    MembershipRole.MARKETING_USER: frozenset(
        {
            Permission.READ,
            Permission.WRITE_CATALOG,
            Permission.RUN_PUBLIC_RESEARCH,
            Permission.RUN_FIT_ASSESSMENT,
            Permission.RUN_QUALIFICATION,
            Permission.SPEND_PROVIDER_CREDITS,
            Permission.GENERATE_CAMPAIGN_DRAFT,
        }
    ),
    MembershipRole.READ_ONLY: frozenset({Permission.READ}),
}


def require_permission(tenant: TenantContext, permission: Permission) -> None:
    if permission not in ROLE_PERMISSIONS[tenant.role]:
        raise AuthorizationError("Your organization role does not permit this action")
