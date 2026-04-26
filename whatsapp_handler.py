"""WhatsApp message orchestrator — ties session, intent, action, and formatting together."""

import logging

from billing_client import billing_client
from whatsapp_session import WhatsAppSessionStore
from whatsapp_intent import classify_whatsapp_intent, WhatsAppIntent
from whatsapp_actions import execute_action
from whatsapp_format import format_wa_reply
from whatsapp_menu import (
    resolve_menu_selection,
    render_main_menu,
    render_document_menu,
    render_support_menu,
    render_invalid_selection,
    SUPPORT_CATEGORIES,
)
from language_detect import detect_user_language

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

    # 2b. Detect and store language preference
    detected_lang = detect_user_language(body)
    if detected_lang != session.language:
        session_store.set_language(from_number, detected_lang)
        session.language = detected_lang
    logger.info("WA language: from=%s detected=%s", from_number, detected_lang)

    # 3a. Check if we're awaiting email verification (account security)
    if session.awaiting_email_verification:
        return await _handle_email_verification(
            session_store, session, from_number, body
        )

    # 3b. Check if we're awaiting account number/name input
    if session.awaiting_account_lookup:
        return await _handle_account_lookup(
            session_store, session, from_number, body
        )

    # 3c. Check if we're awaiting a support ticket description
    if session.awaiting_support_description:
        return await _handle_support_description(
            session_store, session, from_number, body, client_name
        )

    # 4. Check for menu selection BEFORE intent classification
    menu_result = resolve_menu_selection(
        body, session.active_menu_key, session.menu_created_at
    )

    if menu_result:
        return await _handle_menu_selection(
            session_store, session, from_number, body, client_name, menu_result
        )

    # 5. Classify intent via natural language
    intent = classify_whatsapp_intent(body)
    logger.info(
        "WA intent: from=%s action=%s conf=%.2f msg=%s",
        from_number, intent.action, intent.confidence, body[:80],
    )

    # 6. Build reply
    reply_parts: list[str] = []

    # 6a. Greeting on first message in session → show main menu
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

    # 6b. Repeated greeting within active session → show menu again
    if intent.action == "greeting" and not session.needs_greeting:
        reply = render_main_menu(client_name)
        session_store.set_menu(from_number, "main_menu")
        logger.info("Menu shown: main_menu (re-greeting) for %s", from_number)
        session_store.update_after_reply(from_number, body, reply)
        return reply

    # 6c. Unknown intent → show menu
    if intent.action == "unknown":
        reply = render_main_menu(client_name)
        session_store.set_menu(from_number, "main_menu")
        logger.info("Menu shown: main_menu (unknown intent) for %s", from_number)
        session_store.update_after_reply(from_number, body, reply)
        return reply

    # 6d. Support intent via NL → show support category menu
    if intent.action == "support_intake":
        reply = render_support_menu()
        session_store.clear_menu(from_number)
        session_store.set_menu(from_number, "support_menu")
        logger.info("Menu shown: support_menu (NL intent) for %s", from_number)
        session_store.update_after_reply(from_number, body, reply)
        return reply

    # 7. Execute action (natural language matched — clear any active menu)
    session_store.clear_menu(from_number)
    result = await execute_action(intent, session.client_id, session.client_name)

    # 7a. If client is needed, set the account lookup flag
    if result.needs_client:
        session_store.set_awaiting_account_lookup(from_number)

    # 8. Format the action reply
    action_reply = format_wa_reply(result, client_name)

    # 9. Combine greeting + action if this is the first message with an intent
    if reply_parts:
        reply_parts.append("")
        reply_parts.append(action_reply)
        reply = "\n".join(reply_parts)
    else:
        reply = action_reply

    # 10. Avoid repeating identical replies (unless user repeated the same question)
    if reply == session.last_reply:
        reply = "I just sent you that info. Is there anything else I can help with?"

    # 11. Update session
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

    # Sub-menu: support menu
    if action == "_support_menu":
        reply = render_support_menu()
        session_store.set_menu(from_number, "support_menu")
        logger.info("Menu shown: support_menu for %s (from selection '%s')",
                     from_number, body.strip())
        session_store.update_after_reply(from_number, body, reply)
        return reply

    # Support category selected → ask for description
    if action in SUPPORT_CATEGORIES:
        cat = SUPPORT_CATEGORIES[action]
        session_store.clear_menu(from_number)
        session_store.set_support_category(from_number, cat["key"])
        reply = (
            f"*{cat['label']}* \u2014 got it.\n\n"
            "Please describe your issue briefly and I'll create a support ticket for you."
        )
        logger.info("Support category '%s' selected by %s, awaiting description",
                     cat["key"], from_number)
        session_store.update_after_reply(from_number, body, reply)
        return reply

    # Real action — clear menu and execute
    session_store.clear_menu(from_number)
    logger.info("Menu selection resolved: '%s' -> %s for %s",
                body.strip(), action, from_number)

    intent = WhatsAppIntent(action=action, confidence=1.0, raw_message=body)
    result = await execute_action(intent, session.client_id, session.client_name)

    # If client is needed, set the account lookup flag
    if result.needs_client:
        session_store.set_awaiting_account_lookup(from_number)

    reply = format_wa_reply(result, client_name)

    # Avoid repeating identical replies
    if reply == session.last_reply:
        reply = "I just sent you that info. Is there anything else I can help with?"

    session_store.update_after_reply(from_number, body, reply)
    return reply


async def _handle_support_description(
    session_store: WhatsAppSessionStore,
    session,
    from_number: str,
    body: str,
    client_name: str,
) -> str:
    """User sent a description for their support ticket — create it."""
    category = session.support_category or "general"

    # Map category key back to label
    cat_labels = {
        "connectivity": "Connectivity / speed issue",
        "billing": "Billing or payment query",
        "general": "General / other",
    }
    subject = cat_labels.get(category, "General / other")

    # Clear support state immediately
    session_store.clear_support_state(from_number)

    # Create ticket via billing API
    try:
        from billing_client import billing_client
        result = billing_client.create_support_ticket(
            client_id=session.client_id,
            category=category,
            subject=subject,
            message=body.strip(),
            source="whatsapp",
            source_phone=from_number,
        )

        if result.get("success"):
            ticket_number = result.get("ticket_number", "")
            reply = (
                f"Your support ticket *#{ticket_number}* has been created.\n"
                f"Category: {subject}\n\n"
                "Our team will follow up with you shortly. "
                "Is there anything else I can help with?"
            )
            logger.info("Support ticket %s created for %s (category=%s)",
                        ticket_number, from_number, category)
        else:
            error = result.get("error", "unknown")
            logger.error("Failed to create support ticket for %s: %s", from_number, error)
            reply = (
                "I'm sorry, I couldn't create the ticket right now. "
                "Please try again in a moment, or contact us directly for support."
            )
    except Exception as e:
        logger.error("Support ticket creation error for %s: %s", from_number, e, exc_info=True)
        reply = (
            "I'm sorry, something went wrong creating your ticket. "
            "Please try again in a moment."
        )

    session_store.update_after_reply(from_number, body, reply)
    return reply


# ---------------------------------------------------------------------------
# Account lookup + email verification
# ---------------------------------------------------------------------------

def _mask_email(email: str) -> str:
    """Mask email for hint display: c****@example.com"""
    if not email or "@" not in email:
        return "****"
    local, domain = email.split("@", 1)
    if len(local) <= 1:
        return f"{local}****@{domain}"
    return f"{local[0]}{'*' * min(4, len(local) - 1)}@{domain}"


async def _handle_account_lookup(
    session_store: WhatsAppSessionStore,
    session,
    from_number: str,
    body: str,
) -> str:
    """User sent an account number or name — look it up and ask for email verification."""
    query = body.strip()

    # Allow user to bail out
    if query.lower() in ("menu", "hi", "hello", "cancel", "0"):
        session_store.clear_account_lookup_state(from_number)
        reply = render_main_menu("")
        session_store.set_menu(from_number, "main_menu")
        session_store.update_after_reply(from_number, body, reply)
        return reply

    try:
        result = billing_client.client_lookup(query, limit=5)
        clients = result.get("clients", [])
    except Exception as e:
        logger.error("Account lookup error for %s: %s", from_number, e, exc_info=True)
        session_store.clear_account_lookup_state(from_number)
        reply = "Sorry, I couldn't search for that right now. Please try again in a moment."
        session_store.update_after_reply(from_number, body, reply)
        return reply

    if len(clients) == 1:
        c = clients[0]
        email = c.get("email", "") or ""
        client_id = c["id"]
        client_name = c.get("fullname", "")
        client_number = c.get("client_number", "")

        if not email:
            # No email on file — link directly
            session_store.set_awaiting_email_verification(from_number, client_id, client_name, "")
            session_store.confirm_client(from_number)
            reply = (
                f"\u2705 Account *{client_number}* ({client_name}) linked.\n\n"
                + render_main_menu(client_name)
            )
            session_store.set_menu(from_number, "main_menu")
            logger.info("Account %s linked for %s (no email, auto-confirmed)", client_number, from_number)
        else:
            masked = _mask_email(email)
            session_store.set_awaiting_email_verification(from_number, client_id, client_name, email)
            reply = (
                f"I found account *{client_number}* ({client_name}).\n\n"
                f"For security, please confirm the email address on this account.\n"
                f"Hint: {masked}"
            )
            logger.info("Account %s found for %s, awaiting email verification", client_number, from_number)

        session_store.update_after_reply(from_number, body, reply)
        return reply

    if len(clients) > 1:
        reply = (
            f"I found {len(clients)} accounts matching that. "
            "Could you share your account number? It usually starts with *DRA*."
        )
    else:
        reply = (
            "I couldn't find an account matching that. "
            "Please check and try again, or type *menu* to go back."
        )

    session_store.update_after_reply(from_number, body, reply)
    return reply


async def _handle_email_verification(
    session_store: WhatsAppSessionStore,
    session,
    from_number: str,
    body: str,
) -> str:
    """User sent an email to verify their identity — compare against pending client."""
    input_email = body.strip().lower()
    expected_email = (session.pending_client_email or "").lower()

    if input_email == expected_email:
        name = session.pending_client_name
        first_name = name.split()[0] if name else "there"
        session_store.confirm_client(from_number)
        reply = (
            f"\u2705 Verified! Welcome, {first_name}.\n\n"
            + render_main_menu(name)
        )
        session_store.set_menu(from_number, "main_menu")
        logger.info("Email verified for %s — client linked: %s", from_number, name)
        session_store.update_after_reply(from_number, body, reply)
        return reply

    # Failed verification — clear state
    session_store.clear_account_lookup_state(from_number)
    reply = (
        "That doesn't match the email on this account. "
        "For security, I can't provide account information.\n\n"
        "Please send *Hi* to try again."
    )
    logger.info("Email verification failed for %s", from_number)
    session_store.update_after_reply(from_number, body, reply)
    return reply
