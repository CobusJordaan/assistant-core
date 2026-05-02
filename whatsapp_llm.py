"""LLM-powered classifier for free-form WhatsApp messages.

Used as a fallback (or co-classifier) when the pattern-based intent
matcher in `whatsapp_intent.py` returns `unknown`, or when a multi-word
message from a linked client could be misclassified by the cheap
pattern matcher (e.g. "I need help with an invoice" containing the
word "faktuur" but really being a support request).

Talks to the same local Ollama server that powers AI Account Analysis
so no customer data leaves the network.
"""

import json
import logging
import os
import re

import httpx

logger = logging.getLogger("assistant-core.wa-llm")

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
WA_LLM_MODEL = os.getenv("WHATSAPP_LLM_MODEL", os.getenv("DEFAULT_MODEL", "qwen2.5:14b"))
# WhatsApp turns need to feel snappy — keep the budget tight. The classifier
# only ever has to emit a small JSON object so a few hundred predicted tokens
# is plenty.
WA_LLM_TIMEOUT = int(os.getenv("WHATSAPP_LLM_TIMEOUT", "20"))
WA_LLM_NUM_PREDICT = int(os.getenv("WHATSAPP_LLM_NUM_PREDICT", "256"))


SYSTEM_PROMPT = """You classify short WhatsApp messages from a residential / business internet provider's customers.

Pick exactly ONE intent from this list and return ONLY a JSON object — no markdown, no commentary.

Intents:
- "support_ticket" — the customer wants something a human staff member must action: change of bank/payment/contact details, dispute a charge, request a callback, report a fault, escalate a complaint, ask for an arrangement, anything that says "open a ticket" / "log a ticket" / "I need help with X". Provide:
    "category": "billing" (money/account/contract/debit order/bank), or "connectivity" (internet/wifi/speed/outage/router), or "general"
    "subject":  4-8 word summary of what they want (in English regardless of message language; staff inbox is English)
- "new_connection" — the person is a *prospect* asking about getting a NEW Draadloze line, signing up, package availability, or upgrading to an additional service. Triggers like "I want to sign up", "interested in a new connection", "ek wil aansluit", "do you cover my area", "kan julle by my installeer", "I want to add a second line / extra package".
- "balance"        — asking what they owe / their account balance
- "invoices"       — asking which invoices are unpaid / overdue / outstanding
- "invoice_pdf"    — wants their invoice sent / emailed / a PDF / a copy / a link. If the customer specifies a particular invoice (e.g. "send me invoice 233372", "kan jy faktuur HRS-2602-0030 stuur") set "invoice_number" to the exact code or numeric id they typed (no extra words). Omit "invoice_number" if no invoice is named.
- "statement_pdf"  — wants their statement sent
- "summary"        — wants an account overview / their package details / what they're paying for
- "connection"     — wants their internet line / connection / wifi tested
- "smalltalk"      — greeting, thanks, goodbye, polite chitchat. Provide:
    "reply": one short friendly sentence in the user's language (en or af)
- "unclear"        — none of the above; the assistant should fall back to the menu

Choose "support_ticket" whenever the customer is asking for a human-action change or a question the bot cannot answer from billing data. The word "faktuur"/"invoice" alone is NOT enough to choose invoice_pdf — only choose invoice_pdf if they actually want to receive their invoice. "I need help with an invoice" / "ek het hulp nodig met 'n faktuur" is a support_ticket.

Return ONLY this JSON shape, with only the relevant keys:
{"intent": "...", "category": "...", "subject": "...", "invoice_number": "...", "reply": "..."}"""


VALID_INTENTS = frozenset({
    "support_ticket",
    "new_connection",
    "balance",
    "invoices",
    "invoice_pdf",
    "statement_pdf",
    "summary",
    "connection",
    "smalltalk",
    "unclear",
})

VALID_TICKET_CATEGORIES = frozenset({"billing", "connectivity", "general"})


def _extract_json(text: str) -> dict | None:
    """Best-effort JSON parse from LLM output."""
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


def _normalise(parsed: dict | None) -> dict:
    """Return a sanitised classifier result with only known keys / values."""
    if not isinstance(parsed, dict):
        return {"intent": "unclear"}

    intent = parsed.get("intent")
    if intent not in VALID_INTENTS:
        return {"intent": "unclear"}

    out: dict = {"intent": intent}

    if intent == "support_ticket":
        category = parsed.get("category") or "general"
        if not isinstance(category, str) or category not in VALID_TICKET_CATEGORIES:
            category = "general"
        subject = parsed.get("subject") or "Customer request"
        if not isinstance(subject, str):
            subject = "Customer request"
        subject = subject.strip()[:200] or "Customer request"
        out["category"] = category
        out["subject"] = subject

    elif intent == "invoice_pdf":
        inv_num = parsed.get("invoice_number") or ""
        if isinstance(inv_num, str):
            cleaned = inv_num.strip().strip("*").strip()
            # Accept anything that looks like an invoice code: alphanumerics +
            # optional dashes/slashes, 3-50 chars, no whitespace.
            if cleaned and 3 <= len(cleaned) <= 50 and not any(c.isspace() for c in cleaned):
                out["invoice_number"] = cleaned

    elif intent == "smalltalk":
        reply = parsed.get("reply") or ""
        if not isinstance(reply, str):
            reply = ""
        out["reply"] = reply.strip()[:400]

    return out


async def classify_with_llm(message: str, client: dict | None, lang: str) -> dict:
    """Classify a free-form WhatsApp message via local Ollama.

    Returns a dict with at least an `intent` key. On any failure (Ollama
    down, timeout, malformed reply) returns `{"intent": "unclear"}` so
    the caller can safely fall back to the menu.
    """
    if not (message or "").strip():
        return {"intent": "unclear"}

    # We deliberately do NOT include the customer's billing data in the
    # prompt — the classifier only needs the message + the language hint.
    # Anything richer would risk leaking PII into the model context.
    user_msg = (
        f"Message language: {lang}\n"
        f"Customer message: {message.strip()!r}\n"
        "Classify and return JSON."
    )

    timeouts = httpx.Timeout(connect=5.0, read=float(WA_LLM_TIMEOUT), write=5.0, pool=5.0)
    try:
        async with httpx.AsyncClient(timeout=timeouts) as http:
            resp = await http.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": WA_LLM_MODEL,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_msg},
                    ],
                    "stream": False,
                    "format": "json",
                    "options": {
                        "temperature": 0.1,
                        "num_predict": WA_LLM_NUM_PREDICT,
                    },
                },
            )
        if resp.status_code != 200:
            logger.warning("LLM classify HTTP %s: %s", resp.status_code, resp.text[:200])
            return {"intent": "unclear"}
        data = resp.json()
        text = (data.get("message", {}) or {}).get("content", "").strip()
    except httpx.ConnectError:
        logger.warning("LLM classify: cannot reach Ollama at %s", OLLAMA_URL)
        return {"intent": "unclear"}
    except httpx.TimeoutException:
        logger.warning("LLM classify timed out after %ds", WA_LLM_TIMEOUT)
        return {"intent": "unclear"}
    except Exception as e:
        logger.warning("LLM classify error: %s", e)
        return {"intent": "unclear"}

    parsed = _extract_json(text)
    result = _normalise(parsed)
    logger.info(
        "LLM classify: msg=%r -> intent=%s%s",
        message[:80],
        result.get("intent"),
        f" cat={result.get('category')} subj={result.get('subject')!r}"
        if result.get("intent") == "support_ticket" else "",
    )
    return result
