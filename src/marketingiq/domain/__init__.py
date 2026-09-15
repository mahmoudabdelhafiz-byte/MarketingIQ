"""Domain model and provider ports."""

from sqlalchemy import Index, UniqueConstraint

from marketingiq.domain.models import *  # noqa: F403
from marketingiq.domain.models import Base


def _replace_text_unique_constraint(
    table_name: str,
    parent_column: str,
    index_name: str,
) -> None:
    """Keep long criterion values while making their uniqueness index MySQL-compatible."""
    table = Base.metadata.tables[table_name]
    expected = {parent_column, "kind", "value"}
    for constraint in list(table.constraints):
        if isinstance(constraint, UniqueConstraint):
            columns = {column.name for column in constraint.columns}
            if columns == expected:
                table.constraints.remove(constraint)
                break
    if index_name not in {index.name for index in table.indexes}:
        Index(
            index_name,
            table.c[parent_column],
            table.c.kind,
            table.c.value,
            unique=True,
            mysql_length={"value": 640},
        )


_replace_text_unique_constraint(
    "product_criteria",
    "product_id",
    "uq_product_criteria_product_kind_value",
)
_replace_text_unique_constraint(
    "icp_criteria",
    "icp_id",
    "uq_icp_criteria_icp_kind_value",
)
