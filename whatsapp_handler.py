"""WhatsApp message orchestrator — ties session, intent, action, and formatting together."""

import logging

from whatsapp_session import WhatsAppSessionStore
from whatsapp_intent import classify_whatsapp_intent
from whatsapp_actions import execute_action
from whatsapp_format import format_wa_greeting, format_wa_reply

logger = logging.getLogger("assistant-core.wa-handler")

FALLBACK_REPLY = "Sorry, something went wrong. Please try again in a moment."


async def handle_whatsapp_message(
    session_store: WhatsAppSessionStore,
    message_id: str,
    from_number: str,
    body: str,
    profile_name: str,
    client: dict | None,
) -> str:
    """Process one inbound WhatsApp message and return the reply text.

    Single entry point called from /internal/whatsapp/inbound.
    """
    # 1. Resolve client info from billing payload
    client_id = client.get("id") if client else None
    client_name = client.get("fullname", "") if client else ""

    # 2. Load or create session (auto-resets after 30 min inactivity)
    session = session_store.get_or_create(from_number, client_id, client_name)

    # 3. Classify intent
    intent = classify_whatsapp_intent(body)
    logger.info(
        "WA intent: from=%s action=%s conf=%.2f msg=%s",
        from_number, intent.action, intent.confidence, body[:80],
    )

    # 4. Build reply
    reply_parts: list[str] = []

    # 4a. Greeting on first message in session
    if session.needs_greeting:
        greeting = format_wa_greeting(client_name)
        reply_parts.append(greeting)
        session_store.mark_greeted(from_number)

        # Pure greeting intent → return just the greeting
        if intent.action == "greeting":
            session_store.update_after_reply(from_number, body, greeting)
            return greeting

    # 4b. Repeated greeting within active session → short acknowledgement
    if intent.action == "greeting" and not session.needs_greeting:
        reply = "How can I help you?"
        if session.last_reply == reply:
            reply = "I'm here \u2014 what can I help you with?"
        session_store.update_after_reply(from_number, body, reply)
        return reply

    # 5. Execute action
    result = await execute_action(intent, session.client_id, session.client_name)

    # 6. Format the action reply
    action_reply = format_wa_reply(result, client_name)

    # 7. Combine greeting + action if this is the first message with an intent
    if reply_parts:
        reply_parts.append("")
        reply_parts.append(action_reply)
        reply = "\n".join(reply_parts)
    else:
        reply = action_reply

    # 8. Avoid repeating identical replies (unless user repeated the same question)
    if reply == session.last_reply:
        reply = "I just sent you that info. Is there anything else I can help with?"

    # 9. Update session
    session_store.update_after_reply(from_number, body, reply)

    return reply
