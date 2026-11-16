"""Cost per lead, reply and meeting: recorded usage × the organization's unit prices.

Usage comes from records MarketingIQ already keeps (provider credits, AI tokens, paid AI web
searches); results from the outreach funnel. Prices are the organization's own settings, so the
report is an estimate, labelled as such.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from marketingiq.application.authorization import Permission, require_permission
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.campaign_ai import CampaignAIGenerationAttempt
from marketingiq.domain.campaigns import CampaignDraft
from marketingiq.domain.costs import OrganizationCostSettings
from marketingiq.domain.engagement import EngagementEventType, OutreachEngagementEvent
from marketingiq.domain.follow_ups import FollowUpStatus, OutboundFollowUp
from marketingiq.domain.models import (
    AuditLog,
    ContactCandidate,
    ContactEmail,
    EmailVerificationStatus,
    LeadQualification,
    OrganizationCompany,
    Product,
    ProviderUsage,
    QualificationStatus,
)
from marketingiq.domain.outbound import OutboundSendAttempt, OutboundSendStatus
from marketingiq.domain.pipeline import (
    OpportunityStage,
    SalesOpportunity,
    SalesOpportunityStageEvent,
)

AI_SEARCH_ACTION = "company_discovery.ai_search"
REPLY_SUGGESTION_ACTION = "reply.suggested"
REPLY_EVENTS = (
    EngagementEventType.REPLIED,
    EngagementEventType.POSITIVE_REPLY,
    EngagementEventType.NEGATIVE_REPLY,
)
PRICE_FIELDS = (
    "provider_credit_cost",
    "ai_input_per_million",
    "ai_output_per_million",
    "web_search_cost",
    "monthly_fixed_cost",
)
MAX_DAYS = 366


def _money(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01")))


def _per(cost: Decimal, count: int) -> float | None:
    return _money(cost / count) if count else None


class CostReportService:
    def __init__(self, session: Session, tenant: TenantContext, now: datetime | None = None):
        self.session = session
        self.tenant = tenant
        self.now = now or datetime.now(UTC)

    # Prices ----------------------------------------------------------------------------------

    def settings(self) -> OrganizationCostSettings | None:
        return self.session.scalar(
            select(OrganizationCostSettings).where(
                OrganizationCostSettings.organization_id == self.tenant.organization_id
            )
        )

    def describe_settings(self) -> dict[str, Any]:
        require_permission(self.tenant, Permission.READ)
        item = self.settings()
        return {
            "currency": item.currency if item else "USD",
            **{name: _float(getattr(item, name, None)) for name in PRICE_FIELDS},
            "updated_at": item.updated_at if item else None,
        }

    def update_settings(self, *, currency: str = "USD", **prices: float | None) -> dict[str, Any]:
        require_permission(self.tenant, Permission.WRITE_CATALOG)
        clean_currency = (currency or "USD").strip().upper()
        if len(clean_currency) != 3 or not clean_currency.isalpha():
            raise ValueError("currency must be a 3-letter code")
        item = self.settings()
        if item is None:
            item = OrganizationCostSettings(
                organization_id=self.tenant.organization_id, created_at=self.now
            )
            self.session.add(item)
        item.currency = clean_currency
        for name in PRICE_FIELDS:
            if name not in prices:
                continue
            value = prices[name]
            if value is not None and (value < 0 or value > 1_000_000):
                raise ValueError(f"{name} must be between 0 and 1,000,000")
            setattr(item, name, None if value is None else Decimal(str(value)))
        item.updated_by_user_id = self.tenant.actor_user_id
        item.updated_at = self.now
        self.session.add(
            AuditLog(
                organization_id=self.tenant.organization_id,
                actor_user_id=self.tenant.actor_user_id,
                action="cost_settings.updated",
                entity_type="organization_cost_settings",
                entity_id=self.tenant.organization_id,
                metadata_json={"currency": clean_currency},
            )
        )
        self.session.flush()
        return self.describe_settings()

    # Report ----------------------------------------------------------------------------------

    def report(self, days: int = 30) -> dict[str, Any]:
        require_permission(self.tenant, Permission.READ)
        days = max(1, min(int(days), MAX_DAYS))
        end = self.now
        start = end - timedelta(days=days)
        org = self.tenant.organization_id
        prices = self.describe_settings()

        usage = self._usage(org, start, end)
        cost_lines = [
            self._line("Provider credits (Hunter)", usage["provider_credits"], "credit",
                       prices["provider_credit_cost"]),
            self._line("AI input tokens", usage["ai_input_tokens"], "1M tokens",
                       prices["ai_input_per_million"], scale=1_000_000),
            self._line("AI output tokens", usage["ai_output_tokens"], "1M tokens",
                       prices["ai_output_per_million"], scale=1_000_000),
            self._line("AI web searches", usage["ai_web_searches"], "search",
                       prices["web_search_cost"]),
        ]  # fmt: skip
        # Hunter does not report credits per request: those requests count as one credit each.
        cost_lines[0]["estimated_quantity"] = usage["provider_credits_estimated"]
        fixed = Decimal(str(prices["monthly_fixed_cost"] or 0)) * Decimal(days) / Decimal(30)
        cost_lines.append(
            {
                "label": "Subscriptions and fixed costs (pro rata)",
                "quantity": days,
                "unit": "day",
                "unit_price": None,
                "cost": _money(fixed),
                "priced": prices["monthly_fixed_cost"] is not None,
            }
        )
        total = sum((Decimal(str(line["cost"])) for line in cost_lines), Decimal(0))

        funnel = self._funnel(org, start, end)
        per_product = self._per_product(org, start, end)
        return {
            "period": {"days": days, "start": start, "end": end},
            "currency": prices["currency"],
            "estimate": True,
            "prices_missing": [
                line["label"]
                for line in cost_lines
                if not line["priced"] and (line["quantity"] or line["unit"] == "day")
            ],
            "usage": usage,
            "cost_lines": cost_lines,
            "total_cost": _money(total),
            "funnel": funnel,
            "unit_costs": {
                "per_company_added": _per(total, funnel["companies_added"]),
                "per_lead": _per(total, funnel["leads"]),
                "per_email_sent": _per(total, funnel["emails_sent"]),
                "per_reply": _per(total, funnel["replies"]),
                "per_meeting": _per(total, funnel["meetings"]),
            },
            "per_product": per_product,
        }

    @staticmethod
    def _line(label, quantity, unit, price, scale: int = 1) -> dict[str, Any]:
        cost = Decimal(str(price or 0)) * Decimal(quantity) / Decimal(scale)
        return {
            "label": label,
            "quantity": quantity,
            "unit": unit,
            "unit_price": price,
            "cost": _money(cost),
            "priced": price is not None,
        }

    def _usage(self, org: str, start: datetime, end: datetime) -> dict[str, int]:
        paid = (
            ProviderUsage.organization_id == org,
            ProviderUsage.requested_at >= start,
            ProviderUsage.requested_at < end,
            ProviderUsage.cache_hit.is_(False),
        )
        credits = self.session.scalar(
            select(func.coalesce(func.sum(ProviderUsage.credits_used), 0)).where(*paid)
        )
        # Hunter does not report credits per request; count each successful paid request as one.
        estimated = self.session.scalar(
            select(func.count(ProviderUsage.id)).where(
                *paid,
                ProviderUsage.credits_used.is_(None),
                ProviderUsage.success.is_(True),
            )
        )
        campaign_in, campaign_out, campaign_calls = self.session.execute(
            select(
                func.coalesce(func.sum(CampaignAIGenerationAttempt.input_tokens), 0),
                func.coalesce(func.sum(CampaignAIGenerationAttempt.output_tokens), 0),
                func.count(CampaignAIGenerationAttempt.id),
            ).where(
                CampaignAIGenerationAttempt.organization_id == org,
                CampaignAIGenerationAttempt.requested_at >= start,
                CampaignAIGenerationAttempt.requested_at < end,
            )
        ).one()
        searches = tokens_in = tokens_out = discovery_calls = reply_calls = 0
        rows = self.session.scalars(
            select(AuditLog.metadata_json).where(
                AuditLog.organization_id == org,
                AuditLog.action.in_((AI_SEARCH_ACTION, REPLY_SUGGESTION_ACTION)),
                AuditLog.created_at >= start,
                AuditLog.created_at < end,
            )
        )
        for meta in rows:
            data = meta or {}
            if "searches" in data or data.get("context"):
                discovery_calls += 1
                searches += _int(data.get("searches"))
            elif data.get("source") == "AI":
                reply_calls += 1
            else:
                continue
            tokens_in += _int(data.get("input_tokens"))
            tokens_out += _int(data.get("output_tokens"))
        return {
            "provider_credits": int(credits or 0) + int(estimated or 0),
            "provider_credits_estimated": int(estimated or 0),
            "ai_input_tokens": int(campaign_in or 0) + tokens_in,
            "ai_output_tokens": int(campaign_out or 0) + tokens_out,
            "ai_web_searches": searches,
            "ai_calls": int(campaign_calls or 0) + discovery_calls + reply_calls,
        }

    def _funnel(self, org: str, start: datetime, end: datetime) -> dict[str, int]:
        def count(query) -> int:
            return int(self.session.scalar(query) or 0)

        return {
            "companies_added": count(
                select(func.count(OrganizationCompany.id)).where(
                    OrganizationCompany.organization_id == org,
                    OrganizationCompany.created_at >= start,
                    OrganizationCompany.created_at < end,
                )
            ),
            "qualified": count(
                select(func.count(func.distinct(LeadQualification.organization_company_id))).where(
                    LeadQualification.organization_id == org,
                    LeadQualification.qualified_at >= start,
                    LeadQualification.qualified_at < end,
                    LeadQualification.status.in_(
                        (QualificationStatus.HIGH_PRIORITY, QualificationStatus.QUALIFIED)
                    ),
                )
            ),
            "contacts_found": count(
                select(func.count(ContactCandidate.id)).where(
                    ContactCandidate.organization_id == org,
                    ContactCandidate.created_at >= start,
                    ContactCandidate.created_at < end,
                )
            ),
            # A lead: a person with a verified business email, ready to contact.
            "leads": count(
                select(func.count(func.distinct(ContactEmail.contact_id)))
                .join(ContactCandidate, ContactCandidate.id == ContactEmail.contact_id)
                .where(
                    ContactCandidate.organization_id == org,
                    ContactEmail.verification_status == EmailVerificationStatus.VALID,
                    ContactEmail.verified_at >= start,
                    ContactEmail.verified_at < end,
                )
            ),
            "emails_sent": count(
                select(func.count(OutboundSendAttempt.id)).where(
                    OutboundSendAttempt.organization_id == org,
                    OutboundSendAttempt.status == OutboundSendStatus.SENT,
                    OutboundSendAttempt.completed_at >= start,
                    OutboundSendAttempt.completed_at < end,
                )
            ),
            "follow_ups_sent": count(
                select(func.count(OutboundFollowUp.id)).where(
                    OutboundFollowUp.organization_id == org,
                    OutboundFollowUp.status == FollowUpStatus.SENT,
                    OutboundFollowUp.sent_at >= start,
                    OutboundFollowUp.sent_at < end,
                )
            ),
            "replies": count(
                select(func.count(func.distinct(OutreachEngagementEvent.send_attempt_id))).where(
                    OutreachEngagementEvent.organization_id == org,
                    OutreachEngagementEvent.event_type.in_(REPLY_EVENTS),
                    OutreachEngagementEvent.occurred_at >= start,
                    OutreachEngagementEvent.occurred_at < end,
                )
            ),
            "meetings": self._stage_count(org, start, end, OpportunityStage.MEETING),
            "won": self._stage_count(org, start, end, OpportunityStage.WON),
        }

    def _stage_count(self, org: str, start: datetime, end: datetime, stage) -> int:
        return int(
            self.session.scalar(
                select(func.count(func.distinct(SalesOpportunityStageEvent.opportunity_id))).where(
                    SalesOpportunityStageEvent.organization_id == org,
                    SalesOpportunityStageEvent.to_stage == stage,
                    SalesOpportunityStageEvent.occurred_at >= start,
                    SalesOpportunityStageEvent.occurred_at < end,
                )
            )
            or 0
        )

    def _per_product(self, org: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
        rows: dict[str, dict[str, int]] = defaultdict(
            lambda: {"emails_sent": 0, "replies": 0, "meetings": 0, "won": 0}
        )
        for product_id, total in self.session.execute(
            select(CampaignDraft.product_id, func.count(OutboundSendAttempt.id))
            .join(CampaignDraft, CampaignDraft.id == OutboundSendAttempt.draft_id)
            .where(
                OutboundSendAttempt.organization_id == org,
                OutboundSendAttempt.status == OutboundSendStatus.SENT,
                OutboundSendAttempt.completed_at >= start,
                OutboundSendAttempt.completed_at < end,
            )
            .group_by(CampaignDraft.product_id)
        ):
            rows[product_id]["emails_sent"] = int(total)
        for product_id, total in self.session.execute(
            select(
                CampaignDraft.product_id,
                func.count(func.distinct(OutreachEngagementEvent.send_attempt_id)),
            )
            .join(CampaignDraft, CampaignDraft.id == OutreachEngagementEvent.draft_id)
            .where(
                OutreachEngagementEvent.organization_id == org,
                OutreachEngagementEvent.event_type.in_(REPLY_EVENTS),
                OutreachEngagementEvent.occurred_at >= start,
                OutreachEngagementEvent.occurred_at < end,
            )
            .group_by(CampaignDraft.product_id)
        ):
            rows[product_id]["replies"] = int(total)
        for stage, key in ((OpportunityStage.MEETING, "meetings"), (OpportunityStage.WON, "won")):
            for product_id, total in self.session.execute(
                select(
                    SalesOpportunity.product_id,
                    func.count(func.distinct(SalesOpportunityStageEvent.opportunity_id)),
                )
                .join(
                    SalesOpportunity,
                    SalesOpportunity.id == SalesOpportunityStageEvent.opportunity_id,
                )
                .where(
                    SalesOpportunityStageEvent.organization_id == org,
                    SalesOpportunityStageEvent.to_stage == stage,
                    SalesOpportunityStageEvent.occurred_at >= start,
                    SalesOpportunityStageEvent.occurred_at < end,
                )
                .group_by(SalesOpportunity.product_id)
            ):
                rows[product_id][key] = int(total)
        names = dict(
            self.session.execute(
                select(Product.id, Product.name).where(Product.organization_id == org)
            ).all()
        )
        result = []
        for product_id, values in rows.items():
            sent = values["emails_sent"]
            result.append(
                {
                    "product_id": product_id,
                    "product_name": names.get(product_id, "Unknown product"),
                    **values,
                    "reply_rate": round(values["replies"] / sent, 3) if sent else None,
                    "meeting_rate": round(values["meetings"] / sent, 3) if sent else None,
                }
            )
        return sorted(result, key=lambda row: (-row["emails_sent"], row["product_name"]))


def _float(value: Any) -> float | None:
    return None if value is None else float(value)


def _int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0
