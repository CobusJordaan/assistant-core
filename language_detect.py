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

# Strong multi-word phrases → immediate "af" (highest priority)
_STRONG_PHRASES = [
    "hoe gaan dit",
    "kan jy",
    "kan julle",
    "dalk help",
    "my dalk help",
    "help my",
    "wys my",
    "stuur vir my",
    "ek wil",
    "ek soek",
    "my rekening",
    "my faktuur",
    "my fakture",
    "my staat",
    "wat skuld ek",
    "wat is my balans",
    "wat is my saldo",
    "wat is my",
    "waar is my",
    "my internet werk nie",
    "dit werk nie",
    "ek kan nie",
    "ek het",
]

# Single Afrikaans keywords — count hits, >= 2 → "af"
_AF_WORDS = {
    "ek", "jy", "jou", "julle", "ons", "hulle",
    "wat", "waar", "wanneer", "hoe", "hoeveel",
    "rekening", "faktuur", "fakture", "staat",
    "betaling", "betaal", "saldo", "balans",
    "oopstaande", "agterstallig", "kliënt", "klient",
    "diens", "internet", "ondersteuning",
    "nie", "gaan", "het", "is", "was", "sal", "moet", "kan",
    "met", "vir", "van", "die", "dit", "dalk",
}

# English-specific words used by the strict detector to switch *away* from a
# locked Afrikaans session. We deliberately keep this list to words that are
# unambiguous (no Afrikaans homograph) so noisy mixed-language input doesn't
# tip the balance.
_EN_WORDS = {
    "the", "and", "you", "are", "your", "with",
    "what", "where", "when", "why",
    "would", "could", "should", "thanks", "please", "going",
    "want", "need", "send",
    "this", "that", "these", "those", "will", "have", "has", "had",
    "they", "them", "their", "there",
    "from", "about",
    "outstanding", "balance", "account", "invoice", "statement",
    "payment", "due", "owe", "owing",
}


def detect_user_language(text: str) -> str:
    """Detect whether *text* is Afrikaans or English.

    Returns ``"af"`` or ``"en"``.
    Afrikaans always takes priority over English in mixed sentences.
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

    # Bias: question with any Afrikaans word → boost score
    if "?" in lower and hits >= 1:
        hits += 1

    if hits >= 2:
        return "af"

    return "en"


def detect_language_strict(text: str) -> str:
    """Strict variant used to override a *locked* session language.

    Returns ``"af"`` or ``"en"`` only when the message has clear,
    multi-word evidence of that language. Returns ``""`` when the
    signal is too weak (e.g. a single ambiguous word like "my") so
    the caller keeps the existing locked preference instead of
    flipping on noise.

    Rules:
      * Any STRONG_PHRASE match  → "af" (treat as deliberate switch)
      * ≥ 3 unambiguous AF hits AND af_hits > en_hits → "af"
      * ≥ 3 unambiguous EN hits AND en_hits > af_hits → "en"
      * otherwise                                     → ""
    """
    lower = (text or "").lower().strip()
    if not lower:
        return ""

    for phrase in _STRONG_PHRASES:
        if phrase in lower:
            return "af"

    words = [w.strip(".,!?;:'\"()") for w in lower.split()]
    af_hits = sum(1 for w in words if w in _AF_WORDS)
    en_hits = sum(1 for w in words if w in _EN_WORDS)

    if af_hits >= 3 and af_hits > en_hits:
        return "af"
    if en_hits >= 3 and en_hits > af_hits:
        return "en"
    return ""


def get_system_prompt(lang: str) -> str:
    """Return the appropriate system prompt for *lang* (``"af"`` or ``"en"``)."""
    if lang == "af":
        return SYSTEM_PROMPT_AF
    return SYSTEM_PROMPT_EN
