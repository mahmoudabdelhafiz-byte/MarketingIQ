"""Domain model and provider ports."""

from sqlalchemy import CheckConstraint

from marketingiq.domain.models import *  # noqa: F403
from marketingiq.domain.models import Base


def _namespace_check_constraints() -> None:
    """MySQL requires CHECK constraint names to be unique across the database schema."""
    used: set[str] = set()
    for table in Base.metadata.sorted_tables:
        for constraint in table.constraints:
            if not isinstance(constraint, CheckConstraint) or not constraint.name:
                continue
            name = str(constraint.name)
            if name in used:
                name = f"{table.name}_{name}"
            if name in used:
                name = f"{table.name}_{name}_{len(used)}"
            constraint.name = name[:64]
            used.add(constraint.name)


_namespace_check_constraints()
