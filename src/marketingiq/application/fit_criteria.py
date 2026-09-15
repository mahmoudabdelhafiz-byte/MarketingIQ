from typing import Any

from marketingiq.application.intelligence import ConflictStatus, StalenessStatus

SUPPORTED_FACT_KEYS = {
    "industry": ("industry",),
    "country": ("country", "country_code"),
    "employee_min": ("employee_count", "employee_range"),
    "employee_max": ("employee_count", "employee_range"),
    "company_size": ("company_size", "employee_range"),
    "active_hiring": ("active_hiring",),
    "business_service": ("business_services", "business_service"),
    "location": ("locations", "headquarters", "location"),
    "keyword": ("keywords", "company_description"),
    "presence_signal": ("presence_signals", "keywords", "company_description"),
}


def select_projection(
    criterion_type: str, projections: dict[str, dict[str, Any]]
) -> tuple[str | None, dict[str, Any] | None]:
    """Use the workflow's ordered aliases to select a canonical projection."""
    return next(
        (
            (key, projections[key])
            for key in SUPPORTED_FACT_KEYS.get(criterion_type, ())
            if key in projections
        ),
        (None, None),
    )


def projection_quality(projection: dict[str, Any] | None) -> str:
    if projection and projection["conflict_status"] in {
        ConflictStatus.MATERIAL,
        ConflictStatus.NEEDS_REVIEW,
    }:
        return "CONFLICTED"
    if projection and projection["staleness_status"] == StalenessStatus.STALE:
        return "STALE"
    return "CURRENT"
