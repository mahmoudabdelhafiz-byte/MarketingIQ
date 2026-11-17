"""Dashboard: outreach activity over time, funnel, pipeline and recent activity for one period."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from marketingiq.application.authorization import Permission, require_permission
from marketingiq.application.costs import REPLY_EVENTS, CostReportService
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.engagement import OutreachEngagementEvent
from marketingiq.domain.models import Company, ContactCandidate
from marketingiq.domain.outbound import OutboundSendAttempt, OutboundSendStatus
from marketingiq.domain.pipeline import (
    OpportunityStage,
    SalesOpportunity,
    SalesOpportunityStageEvent,
)
from marketingiq.domain.replies import InboundReply

STAGE_ORDER = (
    OpportunityStage.CONTACTED,
    OpportunityStage.RESPONDED,
    OpportunityStage.MEETING,
    OpportunityStage.PROPOSAL,
    OpportunityStage.WON,
    OpportunityStage.LOST,
)
ALLOWED_DAYS = (30, 90, 180, 365)
FUNNEL_STEPS = (
    ("companies_added", "Companies added"),
    ("qualified", "Qualified"),
    ("contacts_found", "Decision makers found"),
    ("leads", "Verified emails"),
    ("emails_sent", "Emails sent"),
    ("replies", "Replies"),
    ("meetings", "Meetings"),
    ("won", "Won"),
)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _week_start(value: datetime) -> datetime:
    day = _aware(value).astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return day - timedelta(days=day.weekday())  # Monday


class DashboardService:
    def __init__(self, session: Session, tenant: TenantContext, now: datetime | None = None):
        self.session = session
        self.tenant = tenant
        self.now = now or datetime.now(UTC)

    def build(self, days: int = 90) -> dict[str, Any]:
        require_permission(self.tenant, Permission.READ)
        days = min(ALLOWED_DAYS, key=lambda allowed: abs(allowed - int(days)))
        end = self.now
        start = end - timedelta(days=days)
        org = self.tenant.organization_id

        current = CostReportService(self.session, self.tenant, now=end).report(days)
        previous = CostReportService(self.session, self.tenant, now=start).report(days)

        sends = list(
            self.session.execute(
                select(
                    OutboundSendAttempt.completed_at,
                    OutboundSendAttempt.company_id,
                    OutboundSendAttempt.contact_id,
                ).where(
                    OutboundSendAttempt.organization_id == org,
                    OutboundSendAttempt.status == OutboundSendStatus.SENT,
                    OutboundSendAttempt.completed_at >= start,
                    OutboundSendAttempt.completed_at < end,
                )
            )
        )
        reply_rows = list(
            self.session.execute(
                select(
                    OutreachEngagementEvent.send_attempt_id,
                    func.min(OutreachEngagementEvent.occurred_at),
                    func.min(OutreachEngagementEvent.organization_company_id),
                )
                .where(
                    OutreachEngagementEvent.organization_id == org,
                    OutreachEngagementEvent.event_type.in_(REPLY_EVENTS),
                    OutreachEngagementEvent.occurred_at >= start,
                    OutreachEngagementEvent.occurred_at < end,
                )
                .group_by(OutreachEngagementEvent.send_attempt_id)
            )
        )
        meeting_rows = list(
            self.session.execute(
                select(
                    SalesOpportunityStageEvent.opportunity_id,
                    func.min(SalesOpportunityStageEvent.occurred_at),
                )
                .where(
                    SalesOpportunityStageEvent.organization_id == org,
                    SalesOpportunityStageEvent.to_stage == OpportunityStage.MEETING,
                    SalesOpportunityStageEvent.occurred_at >= start,
                    SalesOpportunityStageEvent.occurred_at < end,
                )
                .group_by(SalesOpportunityStageEvent.opportunity_id)
            )
        )

        weeks: dict[datetime, dict[str, int]] = {}
        cursor = _week_start(start)
        while cursor < end:
            weeks[cursor] = {"emails_sent": 0, "replies": 0, "meetings": 0}
            cursor += timedelta(days=7)
        for sent_at, _company, _contact in sends:
            weeks.setdefault(_week_start(sent_at), {"emails_sent": 0, "replies": 0, "meetings": 0})
            weeks[_week_start(sent_at)]["emails_sent"] += 1
        for _attempt, occurred_at, _rel in reply_rows:
            weeks.setdefault(
                _week_start(occurred_at), {"emails_sent": 0, "replies": 0, "meetings": 0}
            )
            weeks[_week_start(occurred_at)]["replies"] += 1
        for _opportunity, occurred_at in meeting_rows:
            weeks.setdefault(
                _week_start(occurred_at), {"emails_sent": 0, "replies": 0, "meetings": 0}
            )
            weeks[_week_start(occurred_at)]["meetings"] += 1

        stages = Counter(
            stage
            for (stage,) in self.session.execute(
                select(SalesOpportunity.stage).where(SalesOpportunity.organization_id == org)
            )
        )
        intents = Counter(
            intent.value
            for (intent,) in self.session.execute(
                select(InboundReply.intent).where(
                    InboundReply.organization_id == org,
                    InboundReply.received_at >= start,
                    InboundReply.received_at < end,
                )
            )
        )
        funnel = current["funnel"]
        prev = previous["funnel"]
        sent = funnel["emails_sent"]
        return {
            "period": {"days": days, "start": start, "end": end},
            "currency": current["currency"],
            "kpis": {
                "emails_sent": {"value": sent, "previous": prev["emails_sent"]},
                "reply_rate": {
                    "value": round(funnel["replies"] / sent, 3) if sent else None,
                    "previous": round(prev["replies"] / prev["emails_sent"], 3)
                    if prev["emails_sent"]
                    else None,
                },
                "meetings": {"value": funnel["meetings"], "previous": prev["meetings"]},
                "open_opportunities": {
                    "value": sum(
                        count
                        for stage, count in stages.items()
                        if stage not in (OpportunityStage.WON, OpportunityStage.LOST)
                    ),
                    "previous": None,
                },
                "cost_per_meeting": {
                    "value": current["unit_costs"]["per_meeting"],
                    "previous": previous["unit_costs"]["per_meeting"],
                },
            },
            "weekly": [
                {"week_start": week.date().isoformat(), **values}
                for week, values in sorted(weeks.items())
            ],
            "funnel": [
                {"key": key, "label": label, "value": funnel[key]} for key, label in FUNNEL_STEPS
            ],
            "pipeline": [
                {"stage": stage.value, "count": stages.get(stage, 0)} for stage in STAGE_ORDER
            ],
            "reply_intents": [
                {"intent": intent, "count": count} for intent, count in intents.most_common()
            ],
            "per_product": current["per_product"],
            "total_cost": current["total_cost"],
            "recent": self._recent(org, sends, reply_rows, meeting_rows),
        }

    def _recent(self, org, sends, reply_rows, meeting_rows) -> list[dict[str, Any]]:
        items: list[tuple[datetime, str, str | None, str | None]] = []
        for sent_at, company_id, contact_id in sends:
            items.append((_aware(sent_at), "EMAIL_SENT", company_id, contact_id))
        attempt_ids = [row[0] for row in reply_rows]
        attempts = {
            attempt.id: attempt
            for attempt in self.session.scalars(
                select(OutboundSendAttempt).where(OutboundSendAttempt.id.in_(attempt_ids))
            )
        } if attempt_ids else {}  # fmt: skip
        for attempt_id, occurred_at, _rel in reply_rows:
            attempt = attempts.get(attempt_id)
            if attempt is not None:
                items.append(
                    (_aware(occurred_at), "REPLY", attempt.company_id, attempt.contact_id)
                )
        opportunity_ids = [row[0] for row in meeting_rows]
        opportunities = {
            item.id: item
            for item in self.session.scalars(
                select(SalesOpportunity).where(SalesOpportunity.id.in_(opportunity_ids))
            )
        } if opportunity_ids else {}  # fmt: skip
        for opportunity_id, occurred_at in meeting_rows:
            item = opportunities.get(opportunity_id)
            if item is not None:
                items.append((_aware(occurred_at), "MEETING", item.company_id, item.contact_id))
        items.sort(key=lambda row: row[0], reverse=True)
        items = items[:12]
        companies = {
            company.id: company.canonical_name
            for company in self.session.scalars(
                select(Company).where(Company.id.in_({row[2] for row in items if row[2]}))
            )
        } if items else {}  # fmt: skip
        contacts = {
            contact.id: " ".join(p for p in (contact.first_name, contact.last_name) if p)
            for contact in self.session.scalars(
                select(ContactCandidate).where(
                    ContactCandidate.organization_id == org,
                    ContactCandidate.id.in_({row[3] for row in items if row[3]}),
                )
            )
        } if items else {}  # fmt: skip
        return [
            {
                "at": at,
                "kind": kind,
                "company": companies.get(company_id),
                "contact": contacts.get(contact_id) or None,
            }
            for at, kind, company_id, contact_id in items
        ]
