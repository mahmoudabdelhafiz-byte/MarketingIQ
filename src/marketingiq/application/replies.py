"""Reply assistant: read prospect replies, suggest an answer, offer meeting times, record outcomes.

Replies are captured by the mailbox scanner. Each one gets an intent (rules first, AI on request)
and a suggested answer that a person reviews and sends. The AI may only answer from the product's
value proposition and knowledge base, and only offers the proposed times and booking link.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketingiq.application.authorization import Permission, require_permission
from marketingiq.application.engagement import OutreachEngagementService
from marketingiq.application.errors import ConflictError, NotFoundError
from marketingiq.application.follow_ups import SendWindow
from marketingiq.application.languages import draft_language, pack
from marketingiq.application.outbound import OutboundSenderRegistry
from marketingiq.application.pipeline import (
    STAGE_ORDER,
    TERMINAL_STAGES,
    SalesPipelineService,
    SalesPipelineSyncService,
)
from marketingiq.application.tenant import TenantContext
from marketingiq.application.unsubscribe import UnsubscribeLinks
from marketingiq.domain.campaigns import CampaignDraft, CampaignDraftReviewEvent
from marketingiq.domain.engagement import EngagementEventType
from marketingiq.domain.models import (
    AuditLog,
    Company,
    ContactCandidate,
    ContactEmail,
    Product,
)
from marketingiq.domain.outbound import (
    OutboundMessage,
    OutboundProviderError,
    OutboundSendAttempt,
    SuppressionEntry,
)
from marketingiq.domain.pipeline import OpportunityStage
from marketingiq.domain.replies import InboundReply, ReplyIntent, ReplyStatus

MAX_BODY_CHARS = 8000

# ---------------------------------------------------------------------------------------------
# Reading a reply
# ---------------------------------------------------------------------------------------------

_QUOTE_STARTS = (
    re.compile(r"^\s*On .{3,200}wrote:\s*$", re.IGNORECASE),
    re.compile(r"^\s*-{2,}\s*Original Message\s*-{2,}", re.IGNORECASE),
    re.compile(r"^\s*From:\s.+", re.IGNORECASE),
    re.compile(r"^\s*Sent from my ", re.IGNORECASE),
    re.compile(r"^\s*بتاريخ .{3,200}كتب"),
    re.compile(r"^\s*من:\s.+"),
    re.compile(r"^\s*_{5,}\s*$"),
)


def strip_quoted(text: str) -> str:
    """The new part of a reply: drop the quoted earlier messages and signatures' tails."""
    lines: list[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if line.lstrip().startswith(">"):
            break
        if any(pattern.match(line) for pattern in _QUOTE_STARTS):
            break
        lines.append(line.rstrip())
    cleaned = "\n".join(lines).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return (cleaned or text.strip())[:MAX_BODY_CHARS]


def text_language(text: str, fallback: str = "en") -> str:
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return fallback
    arabic = sum(1 for char in letters if "؀" <= char <= "ۿ")
    return "ar" if arabic / len(letters) >= 0.3 else ("en" if fallback == "ar" else fallback)


def _words(*items: str) -> re.Pattern[str]:
    return re.compile(r"\b(?:" + "|".join(items) + r")\b", re.IGNORECASE)


_RULES: tuple[tuple[ReplyIntent, re.Pattern[str], tuple[str, ...]], ...] = (
    (
        ReplyIntent.UNSUBSCRIBE,
        _words(
            "unsubscribe",
            "remove me",
            "stop (?:emailing|contacting|sending)",
            "do not (?:contact|email)",
            "don't (?:contact|email)",
            "take me off",
            "opt(?:-| )?out",
        ),
        ("إلغاء الاشتراك", "الغاء الاشتراك", "أزيلوا", "توقفوا عن مراسلت", "لا ترسل", "لا تراسل"),
    ),
    (
        ReplyIntent.OUT_OF_OFFICE,
        _words("out of (?:the )?office", "on (?:annual )?leave", "away from the office", "ooo"),
        ("خارج المكتب", "في إجازة", "في اجازة"),
    ),
    (
        ReplyIntent.NOT_INTERESTED,
        _words(
            "not interested",
            "no,? thanks?",
            "no thank you",
            "not a (?:good )?fit",
            "we already (?:have|use)",
            "not relevant",
        ),
        ("غير مهتم", "لسنا مهتمين", "لا نحتاج", "لا حاجة"),
    ),
    (
        ReplyIntent.NOT_NOW,
        _words(
            "not now",
            "not (?:the )?right time",
            "next (?:quarter|year|month)",
            "later this year",
            "(?:reach|circle|follow) (?:out|back|up) (?:later|in)",
            "maybe later",
        ),
        ("ليس الآن", "ليس الان", "لاحقاً", "لاحقا", "في وقت لاحق", "العام القادم"),
    ),
    (
        ReplyIntent.MEETING_REQUEST,
        _words(
            "meeting",
            "meet",
            "call",
            "demo",
            "schedule",
            "calendar",
            "availability",
            "available",
            "let'?s talk",
            "free (?:on|at)",
            "zoom",
            "teams",
        ),
        ("اجتماع", "مكالمة", "عرض توضيحي", "موعد", "متاح", "نلتقي"),
    ),
    (
        ReplyIntent.QUESTION,
        _words("how much", "price", "pricing", "cost", "quote", "how does", "does it", "can it"),
        ("؟", "كم", "السعر", "الأسعار", "التكلفة", "هل يمكن"),
    ),
    (
        ReplyIntent.INTERESTED,
        _words(
            "interested",
            "sounds (?:good|great|interesting)",
            "tell me more",
            "send (?:me )?more",
        ),
        ("مهتم", "مهتمين", "أخبرني المزيد", "أرسل لي", "يبدو جيد"),
    ),
)


def classify_by_rules(text: str) -> tuple[ReplyIntent, float]:
    for intent, english, arabic in _RULES:
        if english.search(text) or any(marker in text for marker in arabic):
            return intent, 0.6
    if "?" in text:
        return ReplyIntent.QUESTION, 0.4
    return ReplyIntent.OTHER, 0.3


# ---------------------------------------------------------------------------------------------
# Meeting times
# ---------------------------------------------------------------------------------------------

_AR_WEEKDAYS = ("الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد")
_EN_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_CITY_AR = {"Riyadh": "الرياض", "Cairo": "القاهرة", "Dubai": "دبي"}


def propose_slots(
    now: datetime, window: SendWindow, hours: tuple[int, ...] = (10, 14)
) -> list[str]:
    """Two meeting times on different working days, starting the next working day."""
    local = now.astimezone(window.timezone)
    slots: list[str] = []
    for offset in range(1, 15):
        day = (local + timedelta(days=offset)).date()
        if day.weekday() not in window.weekdays:
            continue
        hour = hours[len(slots) % len(hours)]
        slot = datetime(day.year, day.month, day.day, hour, tzinfo=window.timezone)
        slots.append(slot.isoformat())
        if len(slots) == 2:
            break
    return slots


def slot_label(value: str, language: str, timezone: Any = None) -> str:
    """Readable time in the meeting time zone (FOLLOW_UP_TIMEZONE unless given)."""
    zone = timezone or SendWindow.from_env().timezone
    moment = datetime.fromisoformat(value).astimezone(zone)
    city = str(getattr(zone, "key", "UTC")).split("/")[-1].replace("_", " ")
    if language == "ar":
        return (
            f"{_AR_WEEKDAYS[moment.weekday()]} {moment.day}/{moment.month} "
            f"الساعة {moment:%H:%M} بتوقيت {_CITY_AR.get(city, city)}"
        )
    weekday = _EN_WEEKDAYS[moment.weekday()]
    return f"{weekday} {moment.day} {moment:%b}, {moment:%H:%M} ({city} time)"


# ---------------------------------------------------------------------------------------------
# Suggested answers without AI
# ---------------------------------------------------------------------------------------------

_TEMPLATES = {
    "en": {
        "thanks": "Thanks for your reply.",
        "meeting": "Happy to set up a short call. Would either of these work for you: {slots}?",
        "booking": "Or pick any time that suits you here: {url}",
        "question": (
            "Thanks for your question. Let me confirm the details and come back to you shortly."
        ),
        "question_call": "If it's easier to discuss, would {slots} work?",
        "not_now": (
            "Understood, thanks for letting me know. Just reply to this email whenever the "
            "timing is better."
        ),
        "not_interested": "Thanks for letting me know. I won't follow up further. All the best.",
        "unsubscribe": "Done. You have been removed from our list and won't hear from us again.",
        "or": " or ",
    },
    "ar": {
        "thanks": "شكراً لردكم.",
        "meeting": "يسعدني ترتيب مكالمة قصيرة. هل يناسبكم أحد هذين الموعدين: {slots}؟",
        "booking": "أو يمكنكم اختيار الوقت المناسب لكم من هنا: {url}",
        "question": "شكراً على سؤالكم. سأتأكد من التفاصيل وأعود إليكم قريباً.",
        "question_call": "وإن كان النقاش أسهل، هل يناسبكم {slots}؟",
        "not_now": "مفهوم، وشكراً لإبلاغي. يكفي الرد على هذه الرسالة متى كان التوقيت أنسب.",
        "not_interested": "شكراً لإبلاغي، لن أتابع مرة أخرى. مع أطيب التمنيات.",
        "unsubscribe": "تم. لقد أزلنا عنوانكم من قائمتنا ولن تصلكم رسائل أخرى منا.",
        "or": " أو ",
    },
}


def template_answer(
    intent: ReplyIntent,
    *,
    language: str,
    first_name: str | None,
    slots: list[str],
    booking_url: str | None,
) -> str | None:
    if intent == ReplyIntent.OUT_OF_OFFICE:
        return None
    lang = language if language in _TEMPLATES else "en"
    words = _TEMPLATES[lang]
    phrases = pack(lang)
    name = (first_name or "").strip()
    greeting = phrases.greeting_named.format(first_name=name) if name else phrases.greeting
    times = words["or"].join(slot_label(slot, lang) for slot in slots)
    lines: list[str]
    if intent in (ReplyIntent.MEETING_REQUEST, ReplyIntent.INTERESTED, ReplyIntent.OTHER):
        lines = [words["thanks"]]
        if times:
            lines.append(words["meeting"].format(slots=times))
        if booking_url:
            lines.append(words["booking"].format(url=booking_url))
    elif intent == ReplyIntent.QUESTION:
        lines = [words["question"]]
        if times:
            lines.append(words["question_call"].format(slots=times))
    elif intent == ReplyIntent.NOT_NOW:
        lines = [words["not_now"]]
    elif intent == ReplyIntent.NOT_INTERESTED:
        lines = [words["not_interested"]]
    else:  # UNSUBSCRIBE
        lines = [words["unsubscribe"]]
    return "\n\n".join([greeting, *lines, phrases.closing])


def reply_subject(original: str) -> str:
    clean = (original or "").strip()
    return clean[:255] if clean.lower().startswith("re:") else f"Re: {clean}"[:255]


# ---------------------------------------------------------------------------------------------
# AI suggestions
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ReplyAIRequest:
    language: str
    language_name: str
    prospect_first_name: str | None
    company_name: str
    product_name: str
    value_proposition: str
    knowledge_base: str
    booking_url: str | None
    proposed_times: tuple[str, ...]
    original_subject: str
    original_body: str
    reply_text: str


@dataclass(frozen=True)
class ReplyAIResult:
    intent: ReplyIntent
    confidence: float | None
    summary: str
    subject: str
    body: str
    model: str


class ReplyAIProvider(Protocol):
    model: str

    @property
    def configured(self) -> bool: ...

    def suggest(self, request: ReplyAIRequest) -> ReplyAIResult: ...


class ReplyAIError(RuntimeError):
    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


_CURRENCY = r"(?:\$|usd|sar|egp|aed|eur|€|£|ريال|دولار|جنيه|درهم)"
_AMOUNT_RE = re.compile(
    rf"{_CURRENCY}\s?(\d[\d,.]*)|(\d[\d,.]*)\s?{_CURRENCY}", re.IGNORECASE
)
_URL_RE = re.compile(r"https?://[^\s<>()\"']+", re.IGNORECASE)
_FORBIDDEN = ("guarantee", "guaranteed", "نضمن", "مضمون")


def validate_ai_answer(body: str, request: ReplyAIRequest) -> None:
    """Reject answers that bring in links, prices or promises the knowledge base does not hold."""
    if not body.strip():
        return
    if len(body) > 5000:
        raise ValueError("Answer is too long")
    source = f"{request.value_proposition}\n{request.knowledge_base}"
    allowed_urls = set(_URL_RE.findall(source))
    if request.booking_url:
        allowed_urls.add(request.booking_url)
    for url in _URL_RE.findall(body):
        if url.rstrip(".,;:") not in {item.rstrip(".,;:") for item in allowed_urls}:
            raise ValueError("Answer contains a link that is not in the knowledge base")
    source_digits = re.sub(r"[,\s]", "", source)
    for match in _AMOUNT_RE.finditer(body):
        amount = re.sub(r"[,\s]", "", match.group(1) or match.group(2) or "").rstrip(".")
        if amount and amount not in source_digits:
            raise ValueError("Answer contains a price that is not in the knowledge base")
    lowered = body.lower()
    if any(marker in lowered for marker in _FORBIDDEN) and not any(
        marker in source.lower() for marker in _FORBIDDEN
    ):
        raise ValueError("Answer makes a guarantee the knowledge base does not")


# ---------------------------------------------------------------------------------------------
# Capturing replies (mailbox job; runs without a user)
# ---------------------------------------------------------------------------------------------


def capture_reply(
    session: Session,
    attempt: OutboundSendAttempt,
    *,
    message_key: str,
    message_id: str | None,
    from_email: str | None,
    subject: str,
    text: str,
    received_at: datetime,
    now: datetime | None = None,
    window: SendWindow | None = None,
) -> InboundReply | None:
    """Store a matched reply once, with a rules-based intent and a suggested answer."""
    clean_key = message_key[:150]
    existing = session.scalar(
        select(InboundReply).where(
            InboundReply.organization_id == attempt.organization_id,
            InboundReply.message_key == clean_key,
        )
    )
    if existing is not None:
        return existing
    draft = session.get(CampaignDraft, attempt.draft_id)
    contact = session.get(ContactCandidate, attempt.contact_id)
    product = session.get(Product, draft.product_id) if draft else None
    if draft is None or contact is None:
        return None
    body = strip_quoted(text)
    language = text_language(body, draft_language(draft.personalization_snapshot))
    intent, confidence = classify_by_rules(body)
    current = now or datetime.now(UTC)
    slots = propose_slots(current, window or SendWindow.from_env())
    approval = session.get(CampaignDraftReviewEvent, attempt.review_event_id)
    item = InboundReply(
        organization_id=attempt.organization_id,
        organization_company_id=attempt.organization_company_id,
        company_id=attempt.company_id,
        initial_attempt_id=attempt.id,
        draft_id=attempt.draft_id,
        contact_id=attempt.contact_id,
        contact_email_id=attempt.contact_email_id,
        message_key=clean_key,
        message_id=(message_id or None) and message_id[:255],
        from_email=(from_email or None) and from_email[:320],
        subject=(subject or "")[:255],
        body_text=body,
        received_at=received_at,
        status=ReplyStatus.NEW,
        intent=intent,
        intent_source="RULES",
        confidence=confidence,
        language=language,
        suggested_subject=reply_subject(subject or (approval.subject if approval else "")),
        suggested_body=template_answer(
            intent,
            language=language,
            first_name=contact.first_name,
            slots=slots,
            booking_url=product.booking_url if product else None,
        ),
        suggested_slots=slots,
        created_at=current,
        updated_at=current,
    )
    session.add(item)
    session.flush()
    session.add(
        AuditLog(
            organization_id=item.organization_id,
            actor_user_id=None,
            action="reply.captured",
            entity_type="inbound_reply",
            entity_id=item.id,
            metadata_json={"intent": intent.value, "company_id": item.company_id},
        )
    )
    return item


def attempt_for_answer(session: Session, message_ids: tuple[str, ...]) -> str | None:
    """Original send attempt of a conversation, found through one of our sent answers."""
    if not message_ids:
        return None
    ids = set(
        session.scalars(
            select(InboundReply.initial_attempt_id).where(
                InboundReply.answer_message_id.in_(message_ids)
            )
        )
    )
    return next(iter(ids)) if len(ids) == 1 else None


# ---------------------------------------------------------------------------------------------
# The assistant (a signed-in user)
# ---------------------------------------------------------------------------------------------


class ReplyAssistantService:
    def __init__(
        self,
        session: Session,
        tenant: TenantContext,
        registry: OutboundSenderRegistry,
        *,
        ai: ReplyAIProvider | None = None,
        now: datetime | None = None,
        unsubscribe_links: UnsubscribeLinks | None = None,
        window: SendWindow | None = None,
    ) -> None:
        self.session = session
        self.tenant = tenant
        self.registry = registry
        self.ai = ai
        self.now = now or datetime.now(UTC)
        self.unsubscribe_links = unsubscribe_links
        self.window = window or SendWindow.from_env()

    # Reading ---------------------------------------------------------------------------------

    def list(self, status: ReplyStatus | None = None, limit: int = 200) -> list[dict[str, Any]]:
        require_permission(self.tenant, Permission.READ)
        query = select(InboundReply).where(
            InboundReply.organization_id == self.tenant.organization_id
        )
        if status is not None:
            query = query.where(InboundReply.status == status)
        items = self.session.scalars(
            query.order_by(InboundReply.received_at.desc()).limit(max(1, min(limit, 500)))
        )
        return [self.describe(item) for item in items]

    def get(self, reply_id: str) -> InboundReply:
        require_permission(self.tenant, Permission.READ)
        item = self.session.scalar(
            select(InboundReply).where(
                InboundReply.id == reply_id,
                InboundReply.organization_id == self.tenant.organization_id,
            )
        )
        if item is None:
            raise NotFoundError("Reply not found")
        return item

    def describe(self, item: InboundReply) -> dict[str, Any]:
        company = self.session.get(Company, item.company_id)
        contact = self.session.get(ContactCandidate, item.contact_id)
        email = self.session.get(ContactEmail, item.contact_email_id)
        draft = self.session.get(CampaignDraft, item.draft_id)
        product = self.session.get(Product, draft.product_id) if draft else None
        name = " ".join(
            part for part in ((contact.first_name, contact.last_name) if contact else ()) if part
        )
        return {
            "id": item.id,
            "organization_company_id": item.organization_company_id,
            "company_id": item.company_id,
            "company_name": company.canonical_name if company else None,
            "contact_id": item.contact_id,
            "contact_name": name or None,
            "recipient": email.email if email else None,
            "from_email": item.from_email,
            "draft_id": item.draft_id,
            "initial_attempt_id": item.initial_attempt_id,
            "product_id": product.id if product else None,
            "product_name": product.name if product else None,
            "booking_url": product.booking_url if product else None,
            "has_knowledge_base": bool(product and (product.knowledge_base or "").strip()),
            "subject": item.subject,
            "body_text": item.body_text,
            "received_at": item.received_at,
            "status": item.status.value,
            "intent": item.intent.value,
            "intent_source": item.intent_source,
            "confidence": item.confidence,
            "summary": item.summary,
            "language": item.language,
            "suggested_subject": item.suggested_subject,
            "suggested_body": item.suggested_body,
            "suggested_slots": [
                {"start": slot, "label": slot_label(slot, item.language)}
                for slot in (item.suggested_slots or [])
            ],
            "suggestion_model": item.suggestion_model,
            "answer_subject": item.answer_subject,
            "answer_body": item.answer_body,
            "answered_at": item.answered_at,
            "outcome": item.outcome,
        }

    # Suggesting ------------------------------------------------------------------------------

    def suggest(self, reply_id: str) -> InboundReply:
        """Fresh times and an AI-written answer; falls back to the template if AI fails."""
        require_permission(self.tenant, Permission.GENERATE_CAMPAIGN_DRAFT)
        item = self._open(reply_id)
        draft = self.session.get(CampaignDraft, item.draft_id)
        contact = self.session.get(ContactCandidate, item.contact_id)
        product = self.session.get(Product, draft.product_id) if draft else None
        company = self.session.get(Company, item.company_id)
        approval = self._approval(item)
        if draft is None or contact is None or product is None or company is None:
            raise NotFoundError("Reply details not found")
        slots = propose_slots(self.now, self.window)
        item.suggested_slots = slots
        fallback = "TEMPLATE"
        if self.ai is not None and self.ai.configured:
            from marketingiq.application.languages import language_name

            request = ReplyAIRequest(
                language=item.language,
                language_name=language_name(item.language),
                prospect_first_name=(contact.first_name or "").strip() or None,
                company_name=company.canonical_name,
                product_name=product.name,
                value_proposition=(product.value_proposition or "").strip(),
                knowledge_base=(product.knowledge_base or "").strip()[:6000],
                booking_url=(product.booking_url or "").strip() or None,
                proposed_times=tuple(slot_label(slot, item.language) for slot in slots),
                original_subject=approval.subject if approval else "",
                original_body=(approval.body if approval else "")[:3000],
                reply_text=item.body_text[:4000],
            )
            try:
                result = self.ai.suggest(request)
                validate_ai_answer(result.body, request)
            except (ReplyAIError, ValueError) as error:
                fallback = getattr(error, "category", "AI_ANSWER_REJECTED")
            else:
                item.intent = result.intent
                item.intent_source = "AI"
                item.confidence = result.confidence
                item.summary = result.summary[:500] or None
                item.suggested_subject = (result.subject or item.suggested_subject or "")[:255]
                item.suggested_body = result.body.strip() or None
                item.suggestion_model = result.model[:100]
                item.updated_at = self.now
                self._audit("reply.suggested", item, {"source": "AI", "intent": item.intent.value})
                self.session.flush()
                return item
        original_subject = approval.subject if approval else ""
        item.suggested_subject = reply_subject(item.subject or original_subject)
        item.suggested_body = template_answer(
            item.intent,
            language=item.language,
            first_name=contact.first_name,
            slots=slots,
            booking_url=(product.booking_url or "").strip() or None,
        )
        item.suggestion_model = None
        item.updated_at = self.now
        self._audit("reply.suggested", item, {"source": "TEMPLATE", "reason": fallback[:50]})
        self.session.flush()
        return item

    def set_intent(self, reply_id: str, intent: ReplyIntent) -> InboundReply:
        require_permission(self.tenant, Permission.RECORD_ENGAGEMENT)
        item = self.get(reply_id)
        item.intent = intent
        item.intent_source = "USER"
        item.updated_at = self.now
        self.session.flush()
        return item

    # Acting ----------------------------------------------------------------------------------

    def send(
        self, reply_id: str, *, subject: str, body: str, then_unsubscribe: bool = False
    ) -> InboundReply:
        require_permission(self.tenant, Permission.SEND_OUTBOUND_EMAIL)
        item = self._open(reply_id)
        clean_subject, clean_body = subject.strip(), body.strip()
        if not clean_subject or len(clean_subject) > 255:
            raise ValueError("subject must contain 1 to 255 characters")
        if not clean_body or len(clean_body) > 20000:
            raise ValueError("body must contain 1 to 20000 characters")
        email = self.session.get(ContactEmail, item.contact_email_id)
        if email is None:
            raise NotFoundError("Recipient not found")
        if self._suppressed(email.email):
            raise ConflictError("REPLY_RECIPIENT_SUPPRESSED")
        sender = self.registry.get("SMTP")
        if not sender.configured:
            raise ConflictError("REPLY_SMTP_NOT_CONFIGURED")
        attempt = self.session.get(OutboundSendAttempt, item.initial_attempt_id)
        references = tuple(
            value
            for value in (attempt.provider_message_id if attempt else None, item.message_id)
            if value
        )
        try:
            result = sender.send(
                OutboundMessage(
                    recipient=email.email,
                    subject=clean_subject,
                    body=clean_body,
                    idempotency_key=f"reply-{item.id}",
                    # A direct answer to their message: no bulk unsubscribe footer is needed,
                    # but keep it when the conversation is still marketing (not an opt-out).
                    unsubscribe_url=(
                        self.unsubscribe_links.url(item.organization_id, email.email)
                        if self.unsubscribe_links is not None and not then_unsubscribe
                        else None
                    ),
                    in_reply_to=item.message_id,
                    references=references,
                    language=item.language,
                )
            )
            if not result.accepted:
                raise OutboundProviderError("Outbound provider rejected the message")
        except OutboundProviderError as error:
            raise ConflictError(f"REPLY_SEND_FAILED_{error.category}"[:60]) from None
        item.status = ReplyStatus.ANSWERED
        item.answer_subject = clean_subject
        item.answer_body = clean_body
        item.answer_message_id = result.provider_message_id
        item.answered_at = self.now
        item.handled_by_user_id = self.tenant.actor_user_id
        item.updated_at = self.now
        self._audit("reply.answered", item, {"intent": item.intent.value})
        self.session.flush()
        if then_unsubscribe:
            self._record(item, EngagementEventType.OPT_OUT, "UNSUBSCRIBED")
        return item

    def dismiss(self, reply_id: str) -> InboundReply:
        require_permission(self.tenant, Permission.RECORD_ENGAGEMENT)
        item = self._open(reply_id)
        item.status = ReplyStatus.DISMISSED
        item.handled_by_user_id = self.tenant.actor_user_id
        item.updated_at = self.now
        self._audit("reply.dismissed", item, {})
        self.session.flush()
        return item

    def mark_meeting_booked(self, reply_id: str, note: str | None = None) -> InboundReply:
        require_permission(self.tenant, Permission.MANAGE_PIPELINE)
        item = self.get(reply_id)
        self._record(item, EngagementEventType.POSITIVE_REPLY, "MEETING_BOOKED")
        self._move_pipeline(item, OpportunityStage.MEETING, "MEETING_BOOKED", note)
        return item

    def mark_not_interested(self, reply_id: str) -> InboundReply:
        require_permission(self.tenant, Permission.MANAGE_PIPELINE)
        item = self.get(reply_id)
        self._record(item, EngagementEventType.NEGATIVE_REPLY, "NOT_INTERESTED")
        self._move_pipeline(item, OpportunityStage.LOST, "NOT_INTERESTED", None)
        return item

    def unsubscribe(self, reply_id: str) -> InboundReply:
        require_permission(self.tenant, Permission.RECORD_ENGAGEMENT)
        item = self.get(reply_id)
        self._record(item, EngagementEventType.OPT_OUT, "UNSUBSCRIBED")
        if item.status == ReplyStatus.NEW:
            item.status = ReplyStatus.DISMISSED
        return item

    # Internals -------------------------------------------------------------------------------

    def _open(self, reply_id: str) -> InboundReply:
        item = self.get(reply_id)
        if item.status != ReplyStatus.NEW:
            raise ConflictError("REPLY_ALREADY_HANDLED")
        return item

    def _approval(self, item: InboundReply) -> CampaignDraftReviewEvent | None:
        attempt = self.session.get(OutboundSendAttempt, item.initial_attempt_id)
        if attempt is None:
            return None
        return self.session.get(CampaignDraftReviewEvent, attempt.review_event_id)

    def _suppressed(self, email: str) -> bool:
        return (
            self.session.scalar(
                select(SuppressionEntry.id).where(
                    SuppressionEntry.organization_id == self.tenant.organization_id,
                    SuppressionEntry.email_normalized == email.strip().lower(),
                )
            )
            is not None
        )

    def _record(self, item: InboundReply, event_type: EngagementEventType, outcome: str) -> None:
        require_permission(self.tenant, Permission.RECORD_ENGAGEMENT)
        OutreachEngagementService(self.session, self.tenant, now=self.now).record(
            item.organization_company_id,
            item.initial_attempt_id,
            event_type=event_type,
            event_key=f"reply-{event_type.value.lower()}-{item.id}"[:100],
            reason_code=outcome,
            occurred_at=self.now,
        )
        item.outcome = outcome
        item.handled_by_user_id = self.tenant.actor_user_id
        item.updated_at = self.now
        self._audit("reply.outcome", item, {"outcome": outcome})
        self.session.flush()

    def _move_pipeline(
        self, item: InboundReply, stage: OpportunityStage, reason: str, note: str | None
    ) -> None:
        attempt = self.session.get(OutboundSendAttempt, item.initial_attempt_id)
        if attempt is None:
            return
        opportunity = SalesPipelineSyncService(self.session, now=self.now).ensure_for_sent_attempt(
            attempt
        )
        if opportunity is None or opportunity.stage in TERMINAL_STAGES:
            return
        if stage != OpportunityStage.LOST and STAGE_ORDER.get(opportunity.stage, 0) >= STAGE_ORDER[
            stage
        ]:
            return
        SalesPipelineService(self.session, self.tenant, now=self.now).move_stage(
            item.organization_company_id,
            opportunity.id,
            stage,
            note=note,
            reason_code=reason,
        )

    def _audit(self, action: str, item: InboundReply, extra: dict[str, Any]) -> None:
        self.session.add(
            AuditLog(
                organization_id=item.organization_id,
                actor_user_id=self.tenant.actor_user_id,
                action=action,
                entity_type="inbound_reply",
                entity_id=item.id,
                metadata_json={"company_id": item.company_id, **extra},
            )
        )
