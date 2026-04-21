"""WhatsApp message orchestrator — ties session, intent, action, and formatting together."""

import logging

from whatsapp_session import WhatsAppSessionStore
from whatsapp_intent import classify_whatsapp_intent, WhatsAppIntent
from whatsapp_actions import execute_action
from whatsapp_format import format_wa_reply
from whatsapp_menu import (
    resolve_menu_selection,
    render_main_menu,
    render_document_menu,
    render_invalid_selection,
)

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

    # 3. Check for menu selection BEFORE intent classification
    menu_result = resolve_menu_selection(
        body, session.active_menu_key, session.menu_created_at
    )

    if menu_result:
        return await _handle_menu_selection(
            session_store, session, from_number, body, client_name, menu_result
        )

    # 4. Classify intent via natural language
    intent = classify_whatsapp_intent(body)
    logger.info(
        "WA intent: from=%s action=%s conf=%.2f msg=%s",
        from_number, intent.action, intent.confidence, body[:80],
    )

    # 5. Build reply
    reply_parts: list[str] = []

    # 5a. Greeting on first message in session → show main menu
    if session.needs_greeting:
        greeting_menu = render_main_menu(client_name)
        reply_parts.append(greeting_menu)
        session_store.mark_greeted(from_number)
        session_store.set_menu(from_number, "main_menu")
        logger.info("Menu shown: main_menu for %s", from_number)

        # Pure greeting intent → return just the greeting menu
        if intent.action == "greeting":
            session_store.update_after_reply(from_number, body, greeting_menu)
            return greeting_menu

    # 5b. Repeated greeting within active session → show menu again
    if intent.action == "greeting" and not session.needs_greeting:
        reply = render_main_menu(client_name)
        session_store.set_menu(from_number, "main_menu")
        logger.info("Menu shown: main_menu (re-greeting) for %s", from_number)
        session_store.update_after_reply(from_number, body, reply)
        return reply

    # 5c. Unknown intent → show menu
    if intent.action == "unknown":
        reply = render_main_menu(client_name)
        session_store.set_menu(from_number, "main_menu")
        logger.info("Menu shown: main_menu (unknown intent) for %s", from_number)
        session_store.update_after_reply(from_number, body, reply)
        return reply

    # 6. Execute action (natural language matched — clear any active menu)
    session_store.clear_menu(from_number)
    result = await execute_action(intent, session.client_id, session.client_name)

    # 7. Format the action reply
    action_reply = format_wa_reply(result, client_name)

    # 8. Combine greeting + action if this is the first message with an intent
    if reply_parts:
        reply_parts.append("")
        reply_parts.append(action_reply)
        reply = "\n".join(reply_parts)
    else:
        reply = action_reply

    # 9. Avoid repeating identical replies (unless user repeated the same question)
    if reply == session.last_reply:
        reply = "I just sent you that info. Is there anything else I can help with?"

    # 10. Update session
    session_store.update_after_reply(from_number, body, reply)

    return reply


async def _handle_menu_selection(
    session_store: WhatsAppSessionStore,
    session,
    from_number: str,
    body: str,
    client_name: str,
    menu_result: dict,
) -> str:
    """Handle a resolved menu selection."""
    action = menu_result["action"]

    # Invalid selection → re-show the menu
    if action == "_invalid_selection":
        reply = render_invalid_selection(menu_result["menu_key"])
        logger.info("Invalid menu selection '%s' for %s, re-showing %s",
                     body.strip(), from_number, menu_result["menu_key"])
        session_store.update_after_reply(from_number, body, reply)
        return reply

    # Sub-menu: document menu
    if action == "_document_menu":
        reply = render_document_menu()
        session_store.set_menu(from_number, "document_menu")
        logger.info("Menu shown: document_menu for %s (from selection '%s')",
                     from_number, body.strip())
        session_store.update_after_reply(from_number, body, reply)
        return reply

    # Real action — clear menu and execute
    session_store.clear_menu(from_number)
    logger.info("Menu selection resolved: '%s' -> %s for %s",
                body.strip(), action, from_number)

    intent = WhatsAppIntent(action=action, confidence=1.0, raw_message=body)
    result = await execute_action(intent, session.client_id, session.client_name)
    reply = format_wa_reply(result, client_name)

    # Avoid repeating identical replies
    if reply == session.last_reply:
        reply = "I just sent you that info. Is there anything else I can help with?"

    session_store.update_after_reply(from_number, body, reply)
    return reply
