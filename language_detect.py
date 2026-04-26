"""Afrikaans / English language detection for Draadloze AI."""


SYSTEM_PROMPT_EN = (
    "You are Draadloze AI, an intelligent assistant integrated with a billing and ISP system. "
    "You help users with account queries, invoices, payments, and support. "
    "Respond clearly, professionally, and concisely in English."
)

SYSTEM_PROMPT_AF = (
    "You are Draadloze AI. The user is communicating in Afrikaans. "
    "Respond in natural, professional South African Afrikaans. "
    "Do not translate literally. Keep the answer clear, friendly, and useful. "
    "Use English technical terms only where commonly used in South Africa."
)

# Strong multi-word phrases → immediate "af"
_STRONG_PHRASES = [
    "my internet werk nie",
    "help my",
    "wys my",
    "stuur vir my",
    "kan jy",
    "ek wil",
    "ek soek",
    "my rekening",
    "my faktuur",
]

# Single Afrikaans keywords — count hits, >= 2 → "af"
_AF_WORDS = {
    "ek", "jy", "jou", "julle", "ons", "hulle",
    "wat", "waar", "wanneer", "hoekom", "hoeveel",
    "asseblief", "dankie",
    "rekening", "faktuur", "fakture", "staat",
    "betaling", "betaal", "saldo", "oopstaande", "agterstallig",
    "kliënt", "klient", "diens", "internet", "ondersteuning",
    "nie", "gaan", "het", "was", "sal", "moet", "kan",
    "met", "vir", "van", "die",
}


def detect_user_language(text: str) -> str:
    """Detect whether *text* is Afrikaans or English.

    Returns ``"af"`` or ``"en"``.
    """
    lower = text.lower().strip()
    if not lower:
        return "en"

    # 1. Strong phrases → immediate "af"
    for phrase in _STRONG_PHRASES:
        if phrase in lower:
            return "af"

    # 2. Count Afrikaans word hits
    words = lower.split()
    hits = sum(1 for w in words if w.strip(".,!?;:'\"()") in _AF_WORDS)
    if hits >= 2:
        return "af"

    return "en"


def get_system_prompt(lang: str) -> str:
    """Return the appropriate system prompt for *lang* (``"af"`` or ``"en"``)."""
    if lang == "af":
        return SYSTEM_PROMPT_AF
    return SYSTEM_PROMPT_EN
