from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Protocol


class CampaignGenerationMode(enum.StrEnum):
    DETERMINISTIC = "DETERMINISTIC"
    AI_ASSISTED = "AI_ASSISTED"


class CampaignSubjectStyle(enum.StrEnum):
    DIRECT = "DIRECT"
    RELEVANCE = "RELEVANCE"
    QUESTION = "QUESTION"


class CampaignOpeningStyle(enum.StrEnum):
    ROLE_RELEVANCE = "ROLE_RELEVANCE"
    COMPANY_RELEVANCE = "COMPANY_RELEVANCE"


class CampaignCTAStyle(enum.StrEnum):
    DEFAULT = "DEFAULT"
    SHORT_CALL = "SHORT_CALL"
    OPEN_QUESTION = "OPEN_QUESTION"


@dataclass(frozen=True)
class CampaignEvidenceOption:
    evidence_id: str
    fact_key: str
    value: str


@dataclass(frozen=True)
class CampaignGenerationContext:
    company_name: str
    product_name: str
    value_proposition: str
    contact_first_name: str | None
    contact_job_title: str | None
    recommended_buyer_role: str
    qualification_status: str
    fit_score: int | None
    fit_grade: str | None
    default_call_to_action: str
    evidence_options: tuple[CampaignEvidenceOption, ...]


@dataclass(frozen=True)
class CampaignStrategyResult:
    provider_key: str
    model: str
    subject_style: CampaignSubjectStyle
    opening_style: CampaignOpeningStyle
    cta_style: CampaignCTAStyle
    selected_evidence_id: str | None
    request_identifier: str | None = None


class CampaignStrategyGenerator(Protocol):
    key: str
    costs_credits: bool

    @property
    def configured(self) -> bool: ...

    @property
    def model(self) -> str: ...

    def generate(self, context: CampaignGenerationContext) -> CampaignStrategyResult: ...
