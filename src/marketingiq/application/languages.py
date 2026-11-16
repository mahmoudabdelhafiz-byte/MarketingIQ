"""Draft languages: which language to write an email in, and the fixed phrases for each one.

``auto`` picks the language from the company's country. Only phrases a person can check are
hard-coded here; everything else is written by the AI provider and reviewed before sending.
"""

from __future__ import annotations

from dataclasses import dataclass

AUTO = "auto"
DEFAULT_LANGUAGE = "en"


@dataclass(frozen=True)
class LanguagePack:
    code: str
    name: str  # English name, used in AI instructions
    native_name: str
    rtl: bool
    default_cta: str
    # Deterministic fallback template
    greeting_named: str  # {first_name}
    greeting: str
    relevance_with_title: str  # {job_title} {company} {product}
    relevance: str  # {company} {product}
    subject: str  # {product} {company}
    closing: str
    # Follow-ups
    follow_up_1: tuple[str, ...]  # {product}
    follow_up_2: tuple[str, ...]  # {product}
    quote_header: str  # {date}
    unsubscribe: str


LANGUAGES: dict[str, LanguagePack] = {
    "en": LanguagePack(
        code="en",
        name="English",
        native_name="English",
        rtl=False,
        default_cta=(
            "Would a 20-minute conversation be useful to explore whether this could fit "
            "your priorities?"
        ),
        greeting_named="Hi {first_name},",
        greeting="Hello,",
        relevance_with_title=(
            "Given your role as {job_title} at {company}, I thought {product} may be relevant."
        ),
        relevance="Given your work at {company}, I thought {product} may be relevant.",
        subject="{product} for {company}",
        closing="Best regards,",
        follow_up_1=(
            "I wanted to follow up on my note below about {product}, in case it got buried.",
            "Would it be worth a short conversation?",
        ),
        follow_up_2=(
            "I don't want to fill your inbox, so this is my last note on this. If "
            "{product} isn't a priority right now, no problem at all.",
            "If it is, just reply and I'll send a few times that suit you.",
        ),
        quote_header="On {date}, I wrote:",
        unsubscribe="If you would prefer not to receive further emails from us, unsubscribe here:",
    ),
    "ar": LanguagePack(
        code="ar",
        name="Arabic",
        native_name="العربية",
        rtl=True,
        default_cta="هل يناسبكم حديث قصير لمدة 20 دقيقة لنرى إن كان هذا يتوافق مع أولوياتكم؟",
        greeting_named="مرحباً {first_name}،",
        greeting="مرحباً،",
        relevance_with_title=(
            "بحكم دوركم كـ {job_title} في {company}، رأيت أن {product} قد يكون ذا صلة بعملكم."
        ),
        relevance="بحكم عملكم في {company}، رأيت أن {product} قد يكون ذا صلة بعملكم.",
        subject="{product} لـ {company}",
        closing="مع خالص التحية،",
        follow_up_1=(
            "أردت المتابعة بخصوص رسالتي أدناه عن {product}، تحسباً لأنها لم تصل إلى أعلى بريدكم.",
            "هل يستحق الأمر حديثاً قصيراً؟",
        ),
        follow_up_2=(
            "لا أريد إثقال بريدكم، لذا هذه آخر رسالة مني بهذا الخصوص. "
            "إن لم يكن {product} من أولوياتكم حالياً فلا بأس إطلاقاً.",
            "وإن كان كذلك، يكفي الرد وسأرسل لكم بعض المواعيد المناسبة.",
        ),
        quote_header="بتاريخ {date} كتبت:",
        unsubscribe="إذا كنتم تفضلون عدم تلقي رسائل أخرى منا، يمكنكم إلغاء الاشتراك من هنا:",
    ),
}

# AI-only languages: the provider writes the email; fixed phrases (fallback template, follow-up
# wording, unsubscribe line) stay in English until a reviewed pack is added above.
AI_ONLY_LANGUAGES = {
    "fr": "French",
    "es": "Spanish",
    "de": "German",
    "tr": "Turkish",
    "pt": "Portuguese",
    "it": "Italian",
}

SUPPORTED_LANGUAGES = (*LANGUAGES, *AI_ONLY_LANGUAGES)

_ARABIC_COUNTRIES = {
    "SA", "AE", "EG", "KW", "QA", "BH", "OM", "JO", "LB", "IQ", "SY", "PS", "YE",
    "LY", "SD", "DZ", "TN", "MA", "MR",
}  # fmt: skip
_COUNTRY_LANGUAGE = {
    **{code: "ar" for code in _ARABIC_COUNTRIES},
    "FR": "fr",
    "ES": "es",
    "MX": "es",
    "AR": "es",
    "CO": "es",
    "CL": "es",
    "PE": "es",
    "DE": "de",
    "AT": "de",
    "TR": "tr",
    "PT": "pt",
    "BR": "pt",
    "IT": "it",
}


def language_name(code: str) -> str:
    if code in LANGUAGES:
        return LANGUAGES[code].name
    return AI_ONLY_LANGUAGES.get(code, LANGUAGES[DEFAULT_LANGUAGE].name)


def pack(code: str | None) -> LanguagePack:
    """Fixed phrases for a language, falling back to English for AI-only languages."""
    return LANGUAGES.get(code or DEFAULT_LANGUAGE, LANGUAGES[DEFAULT_LANGUAGE])


def resolve_language(requested: str | None, country_code: str | None) -> str:
    choice = (requested or AUTO).strip().lower()
    if choice == AUTO:
        return _COUNTRY_LANGUAGE.get((country_code or "").strip().upper(), DEFAULT_LANGUAGE)
    if choice not in SUPPORTED_LANGUAGES:
        raise ValueError(f"Unsupported draft language: {requested}")
    return choice


def draft_language(personalization_snapshot: dict | None) -> str:
    language = (personalization_snapshot or {}).get("language")
    return language if language in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE
