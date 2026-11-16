"""Reply assistant logic that needs no database or web stack."""

from datetime import UTC, datetime

import pytest

from marketingiq.application.follow_ups import SendWindow
from marketingiq.application.replies import (
    ReplyAIRequest,
    classify_by_rules,
    propose_slots,
    reply_subject,
    slot_label,
    strip_quoted,
    template_answer,
    text_language,
    validate_ai_answer,
)
from marketingiq.domain.replies import ReplyIntent
from marketingiq.infrastructure.llm_clients import LLMResponse
from marketingiq.infrastructure.mailbox_engagement import extract_reply_content
from marketingiq.infrastructure.reply_ai import LLMReplyAIProvider, build_prompt


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("Please unsubscribe me from this list.", ReplyIntent.UNSUBSCRIBE),
        ("I am out of the office until Sunday.", ReplyIntent.OUT_OF_OFFICE),
        ("Thanks but we are not interested.", ReplyIntent.NOT_INTERESTED),
        ("Not the right time, maybe next quarter.", ReplyIntent.NOT_NOW),
        ("Sure, can we schedule a call on Tuesday?", ReplyIntent.MEETING_REQUEST),
        ("How much does it cost per user?", ReplyIntent.QUESTION),
        ("Sounds interesting, tell me more.", ReplyIntent.INTERESTED),
        ("Recall the details please", ReplyIntent.OTHER),
        ("يرجى إلغاء الاشتراك", ReplyIntent.UNSUBSCRIBE),
        ("نحن غير مهتمين حالياً", ReplyIntent.NOT_INTERESTED),
        ("هل يمكن ترتيب اجتماع الأسبوع القادم", ReplyIntent.MEETING_REQUEST),
        ("كم السعر؟", ReplyIntent.QUESTION),
        ("نعم مهتم بالموضوع", ReplyIntent.INTERESTED),
    ],
)
def test_rules_classify_english_and_arabic(text, intent):
    assert classify_by_rules(text)[0] == intent


def test_strip_quoted_keeps_only_the_new_text():
    text = "Yes, Tuesday works.\n\nOn Mon, 3 Nov 2026 at 09:00, Ali <a@x.com> wrote:\n> Hi Sara"
    assert strip_quoted(text) == "Yes, Tuesday works."
    assert strip_quoted("نعم\n\nبتاريخ 2026-11-01 كتب أحمد:\n> مرحباً") == "نعم"
    assert strip_quoted("> only quote") == "> only quote"


def test_text_language():
    assert text_language("نعم، يناسبني يوم الثلاثاء", "en") == "ar"
    assert text_language("Yes, Tuesday works", "ar") == "en"
    assert text_language("12345", "ar") == "ar"


RIYADH = SendWindow.from_env()


def test_slots_skip_weekend_and_use_two_days():
    thursday_evening = datetime(2026, 11, 19, 17, 0, tzinfo=UTC)  # Thursday 20:00 Riyadh
    slots = [datetime.fromisoformat(slot) for slot in propose_slots(thursday_evening, RIYADH)]
    assert [(s.weekday(), s.hour) for s in slots] == [(6, 10), (0, 14)]  # Sunday 10, Monday 14
    assert slot_label(slots[0].isoformat(), "en") == "Sunday 22 Nov, 10:00 (Riyadh time)"
    assert slot_label(slots[0].isoformat(), "ar") == "الأحد 22/11 الساعة 10:00 بتوقيت الرياض"


def test_templates():
    slots = propose_slots(datetime(2026, 11, 16, 6, tzinfo=UTC), RIYADH)
    meeting = template_answer(
        ReplyIntent.MEETING_REQUEST,
        language="en",
        first_name="Sara",
        slots=slots,
        booking_url="https://cal.example/sara",
    )
    assert meeting.startswith("Hi Sara,") and "Tuesday 17 Nov, 10:00" in meeting
    assert "https://cal.example/sara" in meeting
    arabic = template_answer(
        ReplyIntent.UNSUBSCRIBE, language="ar", first_name=None, slots=slots, booking_url=None
    )
    assert arabic.startswith("مرحباً،") and "أزلنا" in arabic
    assert (
        template_answer(
            ReplyIntent.OUT_OF_OFFICE, language="en", first_name="A", slots=[], booking_url=None
        )
        is None
    )
    assert reply_subject("Re: Hello") == "Re: Hello" and reply_subject("Hello") == "Re: Hello"


def _request(**overrides):
    values = dict(
        language="en",
        language_name="English",
        prospect_first_name="Sara",
        company_name="Acme",
        product_name="CardIQ",
        value_proposition="Digital business cards.",
        knowledge_base="Pricing: 99 SAR per user per year. Docs: https://cardiq.example/docs",
        booking_url="https://cal.example/x",
        proposed_times=("Tuesday 17 Nov, 10:00 (Riyadh time)",),
        original_subject="CardIQ for Acme",
        original_body="Hi Sara",
        reply_text="How much is it?",
    )
    values.update(overrides)
    return ReplyAIRequest(**values)


def test_ai_answer_validation():
    request = _request()
    validate_ai_answer("It is 99 SAR per user. See https://cardiq.example/docs", request)
    validate_ai_answer("Book here: https://cal.example/x.", request)
    with pytest.raises(ValueError, match="link"):
        validate_ai_answer("See https://evil.example", request)
    with pytest.raises(ValueError, match="price"):
        validate_ai_answer("Only $49 this month!", request)
    with pytest.raises(ValueError, match="guarantee"):
        validate_ai_answer("We guarantee results.", request)


class FakeClient:
    def __init__(self, text):
        self.text = text
        self.prompts = []

    def complete(self, prompt, **kwargs):
        self.prompts.append((prompt, kwargs))
        return LLMResponse(self.text)


class FakeSettings:
    provider = None
    model = "fake-model"
    configured = True


def test_llm_provider_parses_and_sends_schema():
    provider = LLMReplyAIProvider(FakeSettings())
    provider._client = FakeClient(
        '{"intent":"question","confidence":1.4,"summary":"Asks price",'
        '"subject":"Re: CardIQ","body":"Hi Sara, it is 99 SAR."}'
    )
    result = provider.suggest(_request())
    assert result.intent == ReplyIntent.QUESTION and result.confidence == 1.0
    prompt, kwargs = provider._client.prompts[0]
    assert kwargs["json_schema"].name == "reply_suggestion"
    assert "99 SAR" in prompt and "proposed_times" in prompt
    assert "English" in build_prompt(_request())


def test_extract_reply_content_prefers_plain_text():
    raw = (
        b"From: Sara <sara@x.com>\r\nSubject: Re: Hi\r\nMIME-Version: 1.0\r\n"
        b'Content-Type: multipart/alternative; boundary="b"\r\n\r\n'
        b"--b\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nYes please\r\n"
        b"--b\r\nContent-Type: text/html; charset=utf-8\r\n\r\n"
        b"<p>Yes <b>please</b></p>\r\n--b--\r\n"
    )
    content = extract_reply_content(raw)
    assert (content.from_email, content.subject, content.text.strip()) == (
        "sara@x.com",
        "Re: Hi",
        "Yes please",
    )
