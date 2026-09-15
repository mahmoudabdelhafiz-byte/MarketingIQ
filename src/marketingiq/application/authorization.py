from sqlalchemy import select
from sqlalchemy.orm import Session

from marketingiq.domain.models import MembershipRole, OrganizationMembership

WRITE_ROLES = {MembershipRole.ORGANIZATION_ADMIN, MembershipRole.MARKETING_USER}
IMPORT_ROLES = {MembershipRole.ORGANIZATION_ADMIN}


def require_membership(
    session: Session,
    organization_id: str,
    user_id: str,
    allowed_roles: set[MembershipRole] | None = None,
) -> OrganizationMembership:
    membership = session.scalar(
        select(OrganizationMembership).where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.user_id == user_id,
        )
    )
    if membership is None or (allowed_roles is not None and membership.role not in allowed_roles):
        raise PermissionError("The actor is not authorized for this organization action")
    return membership
