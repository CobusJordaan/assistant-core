"""Pattern-based intent classifier for WhatsApp messages."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Any


@dataclass
class WhatsAppIntent:
    """Classified intent from a WhatsApp message."""
    action: str           # e.g. "balance_check", "greeting", "unknown"
    confidence: float     # 0.0 to 1.0
    entities: dict = field(default_factory=dict)
    raw_message: str = ""


# ---------------------------------------------------------------------------
# Pattern registry — checked in order, first match wins
# ---------------------------------------------------------------------------

_INTENT_PATTERNS: list[tuple[re.Pattern, str, float, Callable[..., Any] | None]] = []


def _p(pattern: str, action: str, confidence: float = 0.85,
       entity_fn: Callable[..., Any] | None = None):
    _INTENT_PATTERNS.append(
        (re.compile(pattern, re.IGNORECASE), action, confidence, entity_fn)
    )


# --- Balance check ---
_p(
    r"\b("
    r"balance|how much do i owe|what do i owe|what.?s my balance|"
    r"my balance|account balance|outstanding amount|amount owing|"
    r"amount due|check.?my.?balance|owe you|how much must i pay|"
    r"wat skuld ek|hoeveel skuld"
    r")\b",
    "balance_check", 0.90,
)

# --- Unpaid invoices ---
_p(
    r"\b("
    r"unpaid invoices?|outstanding invoices?|overdue invoices?|"
    r"invoices?\s+(that\s+are\s+|still\s+)?unpaid|"
    r"which invoices?|open invoices?|invoices?\s+due|"
    r"invoices?\s+outstanding|list\s+invoices?|"
    r"my invoices?|show\s+invoices?"
    r")\b",
    "unpaid_invoices", 0.90,
)

# --- Client summary / account overview ---
_p(
    r"\b("
    r"my account|account\s+(summary|overview|details|info)|"
    r"client summary|account details|my details|my info|"
    r"tell me about my account|"
    r"my services?|what package|what plan|my package|my plan"
    r")\b",
    "client_summary", 0.85,
)

# --- Send invoice link ---
_p(
    r"\b("
    r"send.{0,15}invoice|email.{0,15}invoice|"
    r"invoice.{0,15}(link|pdf|copy|resend|again)|"
    r"get.{0,10}invoice|need.{0,10}invoice|"
    r"can i (get|have|see).{0,10}invoice|"
    r"latest invoice|last invoice|recent invoice"
    r")\b",
    "send_invoice_link", 0.85,
)

# --- Send statement link ---
_p(
    r"\b("
    r"send.{0,15}statement|email.{0,15}statement|"
    r"statement.{0,15}(link|pdf|copy|resend)|"
    r"get.{0,10}statement|need.{0,10}statement|"
    r"can i (get|have|see).{0,10}statement|"
    r"my statement|account statement"
    r")\b",
    "send_statement_link", 0.85,
)

# --- Latency / connectivity (explicit ping/check) ---
def _extract_host(m: re.Match) -> dict:
    text = m.group(0)
    host_m = re.search(r"(?:ping|check|test)\s+(\S+)", text, re.IGNORECASE)
    if host_m:
        return {"host": host_m.group(1)}
    return {}

_p(
    r"\b(ping\s+\S+|check\s+(connectivity|connection|latency)\s*\S*)\b",
    "latency_check", 0.90, _extract_host,
)

# --- Latency / connectivity (descriptive complaints) ---
_p(
    r"\b("
    r"internet.{0,15}(slow|down|not working|problem|issue|offline)|"
    r"connection.{0,15}(slow|down|problem|issue|dropping)|"
    r"wifi.{0,10}(slow|down|not working|problem)|"
    r"speed.{0,10}(slow|issue|problem|bad|terrible)|"
    r"can.?t connect|no internet|offline|"
    r"network.{0,10}(down|issue|problem)"
    r")\b",
    "latency_check", 0.80,
)

# --- Support intake (broad catch-all, lower confidence) ---
_p(
    r"\b("
    r"support|complaint|something.?s? wrong|broken|fault|"
    r"technical|trouble|struggling"
    r")\b",
    "support_intake", 0.60,
)


# ---------------------------------------------------------------------------
# Greeting detector (separate — only for short, standalone greetings)
# ---------------------------------------------------------------------------

_GREETING_RE = re.compile(
    r"^(hi|hello|hey|howzit|hallo|good\s*(morning|afternoon|evening)|"
    r"sup|yo|g.?day|greetings?|hola|bonjour)"
    r"\s*[!.,\U0001f44b\U0001f600-\U0001f64f]*\s*$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify_whatsapp_intent(message: str) -> WhatsAppIntent:
    """Classify a WhatsApp message into an actionable intent.

    Returns WhatsAppIntent with action, confidence, entities.
    Pure short greetings → action="greeting".
    No match → action="unknown".
    """
    text = message.strip()
    if not text:
        return WhatsAppIntent(action="unknown", confidence=0.0, raw_message=message)

    # Short standalone greeting (≤30 chars)
    if len(text) <= 30 and _GREETING_RE.match(text):
        return WhatsAppIntent(action="greeting", confidence=0.95, raw_message=message)

    # Try each pattern in order
    for pattern, action, confidence, entity_fn in _INTENT_PATTERNS:
        m = pattern.search(text)
        if m:
            entities = entity_fn(m) if entity_fn else {}
            return WhatsAppIntent(
                action=action, confidence=confidence,
                entities=entities, raw_message=message,
            )

    # Check if message starts with a greeting word but has extra content
    first_word = text.split()[0].rstrip(",.!") if text.split() else ""
    if _GREETING_RE.match(first_word):
        # "Hi there" or "Hello, how are you" — treat as greeting
        return WhatsAppIntent(action="greeting", confidence=0.70, raw_message=message)

    return WhatsAppIntent(action="unknown", confidence=0.0, raw_message=message)
