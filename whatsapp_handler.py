"""WhatsApp message orchestrator — ties session, intent, action, and formatting together."""

import logging

from billing_client import billing_client
from whatsapp_session import WhatsAppSessionStore
from whatsapp_intent import classify_whatsapp_intent, WhatsAppIntent
from whatsapp_actions import execute_action
from whatsapp_format import format_wa_reply
from whatsapp_llm import classify_with_llm
from whatsapp_menu import (
    resolve_menu_selection,
    render_main_menu,
    render_menu,
    render_document_menu,
    render_support_menu,
    render_invalid_selection,
    SUPPORT_CATEGORIES,
)
from whatsapp_i18n import t
from language_detect import detect_user_language, detect_language_strict

logger = logging.getLogger("assistant-core.wa-handler")

FALLBACK_REPLY = "Sorry, something went wrong. Please try again in a moment."


def _get_lang(session, detected_lang: str) -> str:
    """Return the effective language: current detection wins, session is fallback."""
    if detected_lang in ("af", "en"):
        return detected_lang
    return session.language or "en"


def _append_followup_menu(reply: str, lang: str) -> str:
    """Append 'anything else?' + numbered menu after a data reply."""
    followup = t(lang, "anything_else")
    menu_body = render_menu("main_menu", "", lang)
    return f"{reply}\n\n{followup}\n\n{menu_body}"


def _connection_ticket_context(data: dict | None) -> str:
    """Build a one-line context string used in the connectivity ticket body."""
    if not data:
        return "connection_check_failed"
    if not data.get("is_online"):
        if data.get("reason"):
            return f"offline: {data.get('reason')}"
        return "offline"
    ping = data.get("ping")
    if ping is None:
        return f"online_no_ip (radius_session_id={data.get('session_start') or '?'})"
    if not ping.get("success"):
        return f"ping_failed ip={data.get('ip_address') or '?'}"
    return "ok"


_LLM_TO_PATTERN_ACTION = {
    "balance": "balance_check",
    "invoices": "unpaid_invoices",
    "invoice_pdf": "send_invoice_link",
    "statement_pdf": "send_statement_link",
    "summary": "client_summary",
    "connection": "connection_check",
}


async def _route_llm_intent(
    session_store: WhatsAppSessionStore,
    session,
    from_number: str,
    body: str,
    client_name: str,
    lang: str,
    llm_result: dict,
) -> str:
    """Execute the action chosen by the LLM classifier.

    Supports:
      - support_ticket: auto-creates a ticket via the billing API using the
        LLM-supplied subject + category and the verbatim message as the body.
      - balance/invoices/invoice_pdf/statement_pdf/summary/connection: routes
        through the standard `execute_action` pipeline so the reply uses the
        same formatters and follow-up menu logic as the pattern-matched path.
      - smalltalk: uses the LLM's short reply followed by the main menu.
    """
    intent = llm_result.get("intent", "unclear")

    # ---- Support ticket auto-creation ----
    if intent == "support_ticket":
        category = llm_result.get("category", "general")
        subject = (llm_result.get("subject") or "Customer request").strip() or "Customer request"
        try:
            ticket = billing_client.create_support_ticket(
                client_id=session.client_id,
                category=category,
                subject=subject,
                message=body,
                source="whatsapp",
                source_phone=from_number,
            )
            if ticket.get("success"):
                ticket_number = ticket.get("ticket_number", "")
                base = t(lang, "ai_ticket_created", ticket=ticket_number, subject=subject)
                reply = _append_followup_menu(base, lang)
                session_store.set_menu(from_number, "main_menu")
                logger.info("LLM routed -> ticket %s (%s/%s) for %s",
                            ticket_number, category, subject, from_number)
                session_store.update_after_reply(from_number, body, reply)
                return reply
            logger.warning("LLM-routed ticket create failed for %s: %s",
                           from_number, ticket.get("error"))
        except Exception as e:
            logger.error("LLM-routed ticket create error for %s: %s",
                         from_number, e, exc_info=True)
        reply = t(lang, "support_ticket_failed") + "\n\n" + render_main_menu(client_name, lang)
        session_store.set_menu(from_number, "main_menu")
        session_store.update_after_reply(from_number, body, reply)
        return reply

    # ---- Reuse existing pattern-action pipeline ----
    if intent in _LLM_TO_PATTERN_ACTION:
        action = _LLM_TO_PATTERN_ACTION[intent]
        entities: dict = {}
        if intent == "invoice_pdf" and llm_result.get("invoice_number"):
            entities["invoice_number"] = llm_result["invoice_number"]
        synthetic = WhatsAppIntent(
            action=action, confidence=1.0, raw_message=body, entities=entities,
        )
        session_store.clear_menu(from_number)
        result = await execute_action(
            synthetic, session.client_id, session.client_name, from_number, lang,
        )
        if result.needs_client:
            session_store.set_awaiting_account_lookup(from_number)
        action_reply = format_wa_reply(result, client_name, lang)
        action_reply = _finalise_action_reply(session_store, from_number, action_reply, result, lang)
        if action_reply == session.last_reply:
            action_reply = t(lang, "repeat_reply")
        session_store.update_after_reply(from_number, body, action_reply)
        return action_reply

    # ---- Smalltalk: LLM-generated short reply + menu ----
    if intent == "smalltalk":
        text = (llm_result.get("reply") or "").strip()
        if text:
            reply = text + "\n\n" + render_menu("main_menu", "", lang)
            session_store.set_menu(from_number, "main_menu")
            session_store.update_after_reply(from_number, body, reply)
            return reply

    # ---- Unclear / fallback: show the menu ----
    reply = render_main_menu(client_name, lang)
    session_store.set_menu(from_number, "main_menu")
    session_store.update_after_reply(from_number, body, reply)
    return reply


def _finalise_action_reply(session_store, from_number: str, action_reply: str,
                           result, lang: str) -> str:
    """Decide whether to append the main menu or a connection-ticket offer
    after an action runs. Returns the final reply text.

    For a connection_check that found an outage, appends the support-ticket
    offer and arms the awaiting_connection_ticket_offer flag so the next
    inbound message routes to _handle_connection_ticket_offer.
    """
    if not result.success or result.needs_client:
        return action_reply

    if result.data and result.data.get("needs_ticket_offer"):
        ctx = _connection_ticket_context(result.data)
        session_store.offer_connection_ticket(from_number, ctx)
        offer = t(lang, "connection_offer_ticket")
        return f"{action_reply}\n\n{offer}"

    session_store.set_menu(from_number, "main_menu")
    return _append_followup_menu(action_reply, lang)


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
    client_pref_lang = ""
    if client:
        raw_pref = client.get("preferred_billing_language") or ""
        if isinstance(raw_pref, str):
            raw_pref = raw_pref.strip().lower()
            if raw_pref in ("en", "af"):
                client_pref_lang = raw_pref

    # 2. Load or create session (auto-resets after 30 min inactivity)
    session = session_store.get_or_create(from_number, client_id, client_name)

    # 2a. Apply the client's preferred billing language as the session default
    #     when the session has no language set yet. We *lock* it because it
    #     reflects an explicit account-level preference — a noisy detection
    #     on one short message shouldn't flip the customer back to English.
    if client_pref_lang and not session.language:
        session_store.lock_language(from_number, client_pref_lang)
        session.language = client_pref_lang
        session.language_locked = True
        logger.info("WA lang locked from preferred_billing_language: %s -> %s",
                    from_number, client_pref_lang)

    # 2b. Choose the effective language for this turn.
    #     - Short numeric/single-word inputs (menu replies) can't be detected
    #       reliably, so keep the session language for those.
    #     - When the language has been *locked* (explicit user choice or
    #       client preference), only a strict / high-evidence detection of
    #       the OTHER language can flip it. This stops noisy single-word
    #       hits ("my", "is") from flipping a customer back to English.
    #     - Otherwise the regular auto-detector decides.
    stripped = body.strip()
    if stripped.isdigit() or len(stripped.split()) <= 1:
        lang = session.language or "en"
    elif session.language_locked and session.language:
        strict = detect_language_strict(body)
        if strict and strict != session.language:
            session_store.lock_language(from_number, strict)
            session.language = strict
            lang = strict
            logger.info("WA language switch (strict, locked): -> %s for %s",
                        strict, from_number)
        else:
            lang = session.language
    else:
        detected_lang = detect_user_language(body)
        lang = _get_lang(session, detected_lang)
        if lang != session.language:
            session_store.set_language(from_number, lang)
            session.language = lang
    logger.info("WA language: from=%s effective=%s locked=%s",
                from_number, lang, session.language_locked)

    # 3a-pre. First-touch language choice (before any link prompt)
    if session.awaiting_language_choice:
        return await _handle_language_choice(
            session_store, session, from_number, body
        )

    # 3a. Strict identity-link flow (for numbers not in clients or whatsapp_links)
    if session.awaiting_link_client_number:
        return await _handle_link_client_number(
            session_store, session, from_number, body, lang
        )
    if session.awaiting_link_contract_id:
        return await _handle_link_contract_id(
            session_store, session, from_number, body, lang
        )
    if session.awaiting_link_email:
        return await _handle_link_email(
            session_store, session, from_number, body, profile_name, lang
        )
    if session.awaiting_unlinked_ticket_offer:
        return await _handle_unlinked_ticket_offer(
            session_store, session, from_number, body, lang
        )
    if session.awaiting_unlinked_ticket_description:
        return await _handle_unlinked_ticket_description(
            session_store, session, from_number, body, lang
        )
    if session.awaiting_connection_ticket_offer:
        return await _handle_connection_ticket_offer(
            session_store, session, from_number, body, client_name, lang
        )

    # 3b. Check if we're awaiting email verification (account security)
    if session.awaiting_email_verification:
        return await _handle_email_verification(
            session_store, session, from_number, body, lang
        )

    # 3c. Check if we're awaiting account number/name input
    if session.awaiting_account_lookup:
        return await _handle_account_lookup(
            session_store, session, from_number, body, lang
        )

    # 3d. Check if we're awaiting a support ticket description
    if session.awaiting_support_description:
        return await _handle_support_description(
            session_store, session, from_number, body, client_name, lang
        )

    # 3e. Cold entry from an unlinked number → kick off the strict identity flow.
    #     The Twilio webhook calls billing's phone matcher (which now checks
    #     client_whatsapp_links first); if that returns no client, we land here.
    #     If the session has no language preference yet (fresh contact, or a
    #     prior link was revoked) we first ask the user to pick a language,
    #     then the link prompt is shown in their chosen language.
    if not client_id:
        if not session.language:
            session_store.set_awaiting_language_choice(from_number)
            session_store.mark_greeted(from_number)
            reply = t("en", "language_choice_prompt")
            logger.info("Language choice requested for unlinked number %s", from_number)
            session_store.update_after_reply(from_number, body, reply)
            return reply

        session_store.start_identity_link(from_number)
        session_store.mark_greeted(from_number)
        reply = t(lang, "link_intro")
        logger.info("Identity-link flow started for unlinked number %s", from_number)
        session_store.update_after_reply(from_number, body, reply)
        return reply

    # 4. Check for menu selection BEFORE intent classification
    menu_result = resolve_menu_selection(
        body, session.active_menu_key, session.menu_created_at
    )

    if menu_result:
        return await _handle_menu_selection(
            session_store, session, from_number, body, client_name, menu_result, lang
        )

    # 5. Classify intent via natural language
    intent = classify_whatsapp_intent(body)
    logger.info(
        "WA intent: from=%s action=%s conf=%.2f lang=%s msg=%s",
        from_number, intent.action, intent.confidence, lang, body[:80],
    )

    # 5b. LLM co-classifier for free-form sentences from linked clients.
    #     The cheap regex matcher is greedy (e.g. "faktuur" anywhere wins),
    #     so for multi-word messages or pattern-misses we ask Ollama to
    #     re-classify with surrounding context. This is what catches things
    #     like "kan jy 'n ticket oopmaak ek moet my bank besonderhede
    #     verander" → support_ticket → auto-creates the ticket.
    if (session.client_id
            and intent.action != "greeting"
            and (intent.action == "unknown" or len(body.split()) >= 4)):
        try:
            llm_result = await classify_with_llm(body, client, lang)
        except Exception as e:
            logger.warning("LLM classify failed (non-fatal): %s", e)
            llm_result = {"intent": "unclear"}
        if llm_result.get("intent") not in (None, "unclear"):
            return await _route_llm_intent(
                session_store, session, from_number, body, client_name, lang, llm_result
            )

    # 6. Build reply
    reply_parts: list[str] = []

    # 6a. Greeting on first message in session → show main menu
    if session.needs_greeting:
        greeting_menu = render_main_menu(client_name, lang)
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
        reply = render_main_menu(client_name, lang)
        session_store.set_menu(from_number, "main_menu")
        logger.info("Menu shown: main_menu (re-greeting) for %s", from_number)
        session_store.update_after_reply(from_number, body, reply)
        return reply

    # 6c. Unknown intent → show menu
    if intent.action == "unknown":
        reply = render_main_menu(client_name, lang)
        session_store.set_menu(from_number, "main_menu")
        logger.info("Menu shown: main_menu (unknown intent) for %s", from_number)
        session_store.update_after_reply(from_number, body, reply)
        return reply

    # 6d. Support intent via NL → show support category menu
    if intent.action == "support_intake":
        reply = render_support_menu(lang)
        session_store.clear_menu(from_number)
        session_store.set_menu(from_number, "support_menu")
        logger.info("Menu shown: support_menu (NL intent) for %s", from_number)
        session_store.update_after_reply(from_number, body, reply)
        return reply

    # 7. Execute action (natural language matched — clear any active menu)
    session_store.clear_menu(from_number)
    result = await execute_action(intent, session.client_id, session.client_name, from_number, lang)

    # 7a. If client is needed, set the account lookup flag
    if result.needs_client:
        session_store.set_awaiting_account_lookup(from_number)

    # 8. Format the action reply
    action_reply = format_wa_reply(result, client_name, lang)

    # 8a. Append follow-up menu — or a ticket offer if connection check failed
    action_reply = _finalise_action_reply(session_store, from_number, action_reply, result, lang)

    # 9. Combine greeting + action if this is the first message with an intent
    if reply_parts:
        reply_parts.append("")
        reply_parts.append(action_reply)
        reply = "\n".join(reply_parts)
    else:
        reply = action_reply

    # 10. Avoid repeating identical replies (unless user repeated the same question)
    if reply == session.last_reply:
        reply = t(lang, "repeat_reply")

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
    lang: str,
) -> str:
    """Handle a resolved menu selection."""
    action = menu_result["action"]

    # Invalid selection → re-show the menu
    if action == "_invalid_selection":
        reply = render_invalid_selection(menu_result["menu_key"], lang)
        logger.info("Invalid menu selection '%s' for %s, re-showing %s",
                     body.strip(), from_number, menu_result["menu_key"])
        session_store.update_after_reply(from_number, body, reply)
        return reply

    # Sub-menu: document menu
    if action == "_document_menu":
        reply = render_document_menu(lang)
        session_store.set_menu(from_number, "document_menu")
        logger.info("Menu shown: document_menu for %s (from selection '%s')",
                     from_number, body.strip())
        session_store.update_after_reply(from_number, body, reply)
        return reply

    # Sub-menu: support menu
    if action == "_support_menu":
        reply = render_support_menu(lang)
        session_store.set_menu(from_number, "support_menu")
        logger.info("Menu shown: support_menu for %s (from selection '%s')",
                     from_number, body.strip())
        session_store.update_after_reply(from_number, body, reply)
        return reply

    # Support category selected → ask for description (or link account first)
    if action in SUPPORT_CATEGORIES:
        cat = SUPPORT_CATEGORIES[action]
        session_store.clear_menu(from_number)

        if not session.client_id:
            # No linked account — ask user to identify first
            session_store.set_support_category(from_number, cat["key"])
            session.support_category = cat["key"]
            session_store.set_awaiting_account_lookup(from_number)
            reply = t(lang, "support_needs_account")
            logger.info("Support category '%s' selected by %s but no client_id, requesting account lookup",
                         cat["key"], from_number)
            session_store.update_after_reply(from_number, body, reply)
            return reply

        session_store.set_support_category(from_number, cat["key"])
        reply = t(lang, "support_category_selected", label=cat["label"])
        logger.info("Support category '%s' selected by %s, awaiting description",
                     cat["key"], from_number)
        session_store.update_after_reply(from_number, body, reply)
        return reply

    # Real action — clear menu and execute
    session_store.clear_menu(from_number)
    logger.info("Menu selection resolved: '%s' -> %s for %s",
                body.strip(), action, from_number)

    intent = WhatsAppIntent(action=action, confidence=1.0, raw_message=body)
    result = await execute_action(intent, session.client_id, session.client_name, from_number, lang)

    # If client is needed, set the account lookup flag
    if result.needs_client:
        session_store.set_awaiting_account_lookup(from_number)

    reply = format_wa_reply(result, client_name, lang)

    # Append follow-up menu — or a ticket offer if connection check failed
    reply = _finalise_action_reply(session_store, from_number, reply, result, lang)

    # Avoid repeating identical replies
    if reply == session.last_reply:
        reply = t(lang, "repeat_reply")

    session_store.update_after_reply(from_number, body, reply)
    return reply


async def _handle_support_description(
    session_store: WhatsAppSessionStore,
    session,
    from_number: str,
    body: str,
    client_name: str,
    lang: str,
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
            reply = t(lang, "support_ticket_created", ticket=ticket_number, category=subject)
            reply = _append_followup_menu(reply, lang)
            session_store.set_menu(from_number, "main_menu")
            logger.info("Support ticket %s created for %s (category=%s)",
                        ticket_number, from_number, category)
        else:
            error = result.get("error", "unknown")
            logger.error("Failed to create support ticket for %s: %s", from_number, error)
            reply = t(lang, "support_ticket_failed")
    except Exception as e:
        logger.error("Support ticket creation error for %s: %s", from_number, e, exc_info=True)
        reply = t(lang, "support_ticket_error")

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
    lang: str,
) -> str:
    """User sent an account number or name — look it up and ask for email verification."""
    query = body.strip()

    # Allow user to bail out
    if query.lower() in ("menu", "hi", "hello", "cancel", "0"):
        session_store.clear_account_lookup_state(from_number)
        reply = render_main_menu("", lang)
        session_store.set_menu(from_number, "main_menu")
        session_store.update_after_reply(from_number, body, reply)
        return reply

    try:
        result = billing_client.client_lookup(query, limit=5)
        clients = result.get("clients", [])
    except Exception as e:
        logger.error("Account lookup error for %s: %s", from_number, e, exc_info=True)
        session_store.clear_account_lookup_state(from_number)
        reply = t(lang, "lookup_error")
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
            linked_msg = t(lang, "lookup_linked", number=client_number, name=client_name)
            logger.info("Account %s linked for %s (no email, auto-confirmed)", client_number, from_number)

            # Resume pending support flow if category was selected before lookup
            if session.support_category:
                cat_labels = {
                    "connectivity": "Connectivity / speed issue",
                    "billing": "Billing or payment query",
                    "general": "General / other",
                }
                label = cat_labels.get(session.support_category, "General / other")
                reply = linked_msg + t(lang, "support_category_selected", label=label)
                logger.info("Resuming support flow for %s after account link", from_number)
            else:
                reply = linked_msg + render_main_menu(client_name, lang)
                session_store.set_menu(from_number, "main_menu")
        else:
            masked = _mask_email(email)
            session_store.set_awaiting_email_verification(from_number, client_id, client_name, email)
            reply = t(lang, "lookup_verify_email", number=client_number, name=client_name, masked=masked)
            logger.info("Account %s found for %s, awaiting email verification", client_number, from_number)

        session_store.update_after_reply(from_number, body, reply)
        return reply

    if len(clients) > 1:
        reply = t(lang, "lookup_multiple", count=len(clients))
    else:
        reply = t(lang, "lookup_none")

    session_store.update_after_reply(from_number, body, reply)
    return reply


async def _handle_email_verification(
    session_store: WhatsAppSessionStore,
    session,
    from_number: str,
    body: str,
    lang: str,
) -> str:
    """User sent an email to verify their identity — compare against pending client."""
    input_email = body.strip().lower()
    expected_email = (session.pending_client_email or "").lower()

    if input_email == expected_email:
        name = session.pending_client_name
        first_name = name.split()[0] if name else "there"
        session_store.confirm_client(from_number)
        verified_msg = t(lang, "email_verified", name=first_name)
        logger.info("Email verified for %s — client linked: %s", from_number, name)

        # Resume pending support flow if category was selected before lookup
        if session.support_category:
            cat_labels = {
                "connectivity": "Connectivity / speed issue",
                "billing": "Billing or payment query",
                "general": "General / other",
            }
            label = cat_labels.get(session.support_category, "General / other")
            reply = verified_msg + t(lang, "support_category_selected", label=label)
            logger.info("Resuming support flow for %s after email verification", from_number)
        else:
            reply = verified_msg + render_main_menu(name, lang)
            session_store.set_menu(from_number, "main_menu")

        session_store.update_after_reply(from_number, body, reply)
        return reply

    # Failed verification — clear state
    session_store.clear_account_lookup_state(from_number)
    reply = t(lang, "email_failed")
    logger.info("Email verification failed for %s", from_number)
    session_store.update_after_reply(from_number, body, reply)
    return reply


# ---------------------------------------------------------------------------
# Strict identity-link flow (unlinked WhatsApp numbers)
#
# Triggered when billing's phone matcher returns no client (no row in clients
# AND no row in client_whatsapp_links). The user must supply client_number,
# contract_id, and email — all three must match the same client row — before
# we persist a link via the billing API. On failure we offer to open an
# unlinked support ticket so staff can follow up manually.
# ---------------------------------------------------------------------------

_LINK_CANCEL_WORDS = {"menu", "cancel", "stop", "exit", "0"}
_TICKET_YES_WORDS = {"yes", "y", "ja", "yep", "ok", "okay"}
_TICKET_NO_WORDS = {"no", "n", "nee", "cancel", "stop"}

# Greetings + obvious noise that shouldn't be accepted as an account number.
# Account numbers / contract IDs in this CRM are alphanumeric and >= 3 chars
# (e.g. DRA0011, SDA000), so a bare "hi" / "1" / "ja" is clearly a misfire.
_ACCOUNT_REF_NOISE_WORDS = {
    "hi", "hello", "hey", "howzit", "hallo", "haai", "hoi", "yo", "sup",
    "more", "môre", "dag", "ja", "yes", "no", "nee", "ok", "okay",
    "thanks", "thank", "dankie", "help",
}


def _trim_link_input(body: str) -> str:
    return body.strip().strip("*").strip()


def _looks_like_account_ref(value: str) -> bool:
    """Cheap sanity check before treating a message as a client_number /
    contract_id. Filters greetings, single-digit menu replies, multi-word
    sentences ("ek wil my internet skuif" — clearly a support request, not
    an ID), and too-short noise so the user gets re-prompted (or LLM-routed
    to a ticket) instead of being marched straight to the email step."""
    v = (value or "").strip()
    if not v:
        return False
    # Account refs in this CRM are a single alphanumeric token (DRA0011 /
    # SDA000). Any whitespace = sentence, never an ID.
    if any(c.isspace() for c in v):
        return False
    # Single digit / very short numeric — probably a menu reply, not an ID
    if v.isdigit() and len(v) < 4:
        return False
    # Greeting / generic acknowledgement
    if v.lower() in _ACCOUNT_REF_NOISE_WORDS:
        return False
    # Real account refs are at least 3 alphanumeric chars
    if len(v) < 3:
        return False
    if not any(c.isalnum() for c in v):
        return False
    return True


_LANG_CHOICE_EN = {"1", "en", "eng", "english", "engels"}
_LANG_CHOICE_AF = {"2", "af", "afr", "afrikaans"}


async def _handle_language_choice(
    session_store: WhatsAppSessionStore,
    session,
    from_number: str,
    body: str,
) -> str:
    """Resolve the first-touch language pick, then fall through to the link
    prompt in the chosen language. Anything unrecognised re-prompts."""
    raw = (body or "").strip().strip("*").strip().lower()

    chosen: str | None = None
    if raw in _LANG_CHOICE_EN:
        chosen = "en"
    elif raw in _LANG_CHOICE_AF:
        chosen = "af"

    if chosen is None:
        reply = t("en", "language_choice_invalid")
        session_store.update_after_reply(from_number, body, reply)
        return reply

    session_store.lock_language(from_number, chosen)
    session.language = chosen
    session.language_locked = True
    session_store.clear_language_choice(from_number)
    session_store.start_identity_link(from_number)
    reply = t(chosen, "link_intro")
    logger.info("Language chosen and locked for %s: %s — proceeding to link_intro",
                from_number, chosen)
    session_store.update_after_reply(from_number, body, reply)
    return reply


async def _unlinked_ticket_from_link_flow(
    session_store: WhatsAppSessionStore,
    from_number: str,
    body: str,
    lang: str,
    llm_result: dict,
) -> str:
    """User asked for support mid-link-flow ("please open a ticket, I don't
    have my account number"). Drop out of the link flow, log an unlinked
    ticket immediately, and confirm — no further prompts needed."""
    subject = (llm_result.get("subject") or "Customer request").strip() or "Customer request"
    category = llm_result.get("category", "general")

    # Exit the link flow — the user has chosen another path
    session_store.clear_link_state(from_number)

    try:
        ticket = billing_client.create_support_ticket(
            client_id=None,
            category=category,
            subject=subject,
            message=body,
            source="whatsapp",
            source_phone=from_number,
        )
        if ticket.get("success"):
            ticket_number = ticket.get("ticket_number", "")
            reply = t(lang, "link_ticket_unlinked_created", ticket=ticket_number)
            logger.info("LLM-routed unlinked ticket %s created mid-link-flow for %s (subj=%r)",
                        ticket_number, from_number, subject)
            session_store.update_after_reply(from_number, body, reply)
            return reply
        logger.warning("Unlinked ticket create failed mid-link-flow for %s: %s",
                       from_number, ticket.get("error"))
    except Exception as e:
        logger.error("Unlinked ticket error mid-link-flow for %s: %s",
                     from_number, e, exc_info=True)

    reply = t(lang, "support_ticket_failed")
    session_store.update_after_reply(from_number, body, reply)
    return reply


async def _handle_link_client_number(
    session_store: WhatsAppSessionStore,
    session,
    from_number: str,
    body: str,
    lang: str,
) -> str:
    """Step 1 of identity link: capture an account reference (matched against
    EITHER `client_number` OR `contract_id`), then jump straight to email
    verification — no separate contract_id prompt."""
    value = _trim_link_input(body)

    if value.lower() in _LINK_CANCEL_WORDS:
        session_store.clear_link_state(from_number)
        session_store.start_identity_link(from_number)
        reply = t(lang, "link_intro")
        session_store.update_after_reply(from_number, body, reply)
        return reply

    if not value:
        reply = t(lang, "link_intro")
        session_store.update_after_reply(from_number, body, reply)
        return reply

    if not _looks_like_account_ref(value):
        # The user typed something that isn't an account number. If it's a
        # multi-word sentence, the LLM gets a chance to detect "I'm actually
        # asking for help" intents — e.g. "Kan jy 'n kaartjie oop maak ek het
        # nie my Rek nommer nie" (please open a ticket, I don't have my
        # account number) — and route to an unlinked ticket instead of
        # looping the user back to the same prompt.
        if len(value.split()) >= 4:
            try:
                llm = await classify_with_llm(value, None, lang)
            except Exception as e:
                logger.warning("LLM classify in link flow failed: %s", e)
                llm = {"intent": "unclear"}
            if llm.get("intent") == "support_ticket":
                return await _unlinked_ticket_from_link_flow(
                    session_store, from_number, body, lang, llm,
                )

        logger.info("Identity-link: %s sent invalid account_ref=%r, re-prompting",
                    from_number, value)
        reply = t(lang, "link_invalid_account_ref")
        session_store.update_after_reply(from_number, body, reply)
        return reply

    session_store.advance_account_ref_to_email(from_number, value)
    reply = t(lang, "link_ask_email")
    logger.info("Identity-link: %s submitted account_ref=%s, awaiting email",
                from_number, value)
    session_store.update_after_reply(from_number, body, reply)
    return reply


async def _handle_link_contract_id(
    session_store: WhatsAppSessionStore,
    session,
    from_number: str,
    body: str,
    lang: str,
) -> str:
    """Step 2 of identity link: capture contract_id, then ask for email."""
    value = _trim_link_input(body)

    if value.lower() in _LINK_CANCEL_WORDS:
        session_store.clear_link_state(from_number)
        session_store.start_identity_link(from_number)
        reply = t(lang, "link_intro")
        session_store.update_after_reply(from_number, body, reply)
        return reply

    if not value:
        reply = t(lang, "link_ask_contract")
        session_store.update_after_reply(from_number, body, reply)
        return reply

    session_store.advance_link_to_email(from_number, value)
    reply = t(lang, "link_ask_email")
    logger.info("Identity-link: %s submitted contract_id=%s, awaiting email",
                from_number, value)
    session_store.update_after_reply(from_number, body, reply)
    return reply


async def _handle_link_email(
    session_store: WhatsAppSessionStore,
    session,
    from_number: str,
    body: str,
    profile_name: str,
    lang: str,
) -> str:
    """Step 3 of identity link: verify all three fields, then persist the link."""
    email = _trim_link_input(body).lower()

    if email in _LINK_CANCEL_WORDS:
        session_store.clear_link_state(from_number)
        session_store.start_identity_link(from_number)
        reply = t(lang, "link_intro")
        session_store.update_after_reply(from_number, body, reply)
        return reply

    account_ref = session.pending_link_client_number or ""
    contract_id = session.pending_link_contract_id or ""  # legacy mid-flight sessions

    if not email or "@" not in email:
        reply = t(lang, "link_ask_email")
        session_store.update_after_reply(from_number, body, reply)
        return reply

    try:
        result = billing_client.verify_client_identity(
            client_number=account_ref, email=email, contract_id=contract_id,
        )
    except Exception as e:
        logger.error("Identity verify error for %s: %s", from_number, e, exc_info=True)
        session_store.clear_link_state(from_number)
        session_store.offer_unlinked_ticket(from_number)
        reply = t(lang, "lookup_error") + "\n\n" + t(lang, "link_offer_ticket")
        session_store.update_after_reply(from_number, body, reply)
        return reply

    if result.get("matched"):
        client = result.get("client") or {}
        client_id = client.get("id")
        c_name = client.get("fullname", "")
        c_number = client.get("client_number", "")

        # Persist the link so subsequent inbound messages auto-resolve to this client
        try:
            billing_client.link_whatsapp(client_id, from_number, profile_name=profile_name)
        except Exception as e:
            logger.error("link_whatsapp error for %s (client=%s): %s",
                         from_number, client_id, e, exc_info=True)
            # Continue anyway — confirm in session at least

        session_store.confirm_identity_link(from_number, client_id, c_name)

        # Honour the client's preferred billing language for the success
        # reply, and *lock* it so subsequent messages stay in that language
        # even if the auto-detector misreads a noisy sentence.
        pref = (client.get("preferred_billing_language") or "").strip().lower()
        if pref in ("en", "af"):
            session_store.lock_language(from_number, pref)
            session.language = pref
            session.language_locked = True
            if pref != lang:
                logger.info("Identity-link: language locked to %s for %s (client preference)",
                            pref, from_number)
                lang = pref

        first_name = c_name.split()[0] if c_name else "there"
        verified_msg = t(lang, "link_success", name=first_name, number=c_number)
        reply = verified_msg + render_main_menu(c_name, lang)
        session_store.set_menu(from_number, "main_menu")
        logger.info("Identity-link verified: %s -> client_id=%s (%s)",
                    from_number, client_id, c_number)
        session_store.update_after_reply(from_number, body, reply)
        return reply

    # Verification failed — offer the support-ticket fallback
    reason = result.get("reason", "")
    if reason == "email_mismatch":
        msg = t(lang, "link_failed_email")
    else:
        msg = t(lang, "link_failed_number")

    session_store.clear_link_state(from_number)
    session_store.offer_unlinked_ticket(from_number)
    reply = f"{msg}\n\n{t(lang, 'link_offer_ticket')}"
    logger.info("Identity-link failed for %s reason=%s", from_number, reason)
    session_store.update_after_reply(from_number, body, reply)
    return reply


async def _handle_unlinked_ticket_offer(
    session_store: WhatsAppSessionStore,
    session,
    from_number: str,
    body: str,
    lang: str,
) -> str:
    """User said yes/no to opening an unlinked support ticket."""
    answer = _trim_link_input(body).lower()

    if answer in _TICKET_YES_WORDS:
        session_store.accept_unlinked_ticket(from_number)
        reply = t(lang, "link_ticket_ask_description")
        session_store.update_after_reply(from_number, body, reply)
        return reply

    if answer in _TICKET_NO_WORDS:
        session_store.clear_unlinked_ticket_state(from_number)
        reply = t(lang, "link_ticket_cancelled")
        session_store.update_after_reply(from_number, body, reply)
        return reply

    # Anything else — re-prompt
    reply = t(lang, "link_offer_ticket")
    session_store.update_after_reply(from_number, body, reply)
    return reply


async def _handle_connection_ticket_offer(
    session_store: WhatsAppSessionStore,
    session,
    from_number: str,
    body: str,
    client_name: str,
    lang: str,
) -> str:
    """User said yes/no after a failed connection check. Yes opens a
    connectivity-category ticket using the stashed diagnostic context."""
    answer = _trim_link_input(body).lower()

    if answer in _TICKET_NO_WORDS:
        session_store.clear_connection_ticket_state(from_number)
        reply = t(lang, "link_ticket_cancelled") + "\n\n" + render_main_menu(client_name, lang)
        session_store.set_menu(from_number, "main_menu")
        session_store.update_after_reply(from_number, body, reply)
        return reply

    if answer not in _TICKET_YES_WORDS:
        # Re-prompt
        reply = t(lang, "connection_offer_ticket")
        session_store.update_after_reply(from_number, body, reply)
        return reply

    context = session.connection_ticket_context or "connection_check_failed"
    session_store.clear_connection_ticket_state(from_number)

    description = (
        f"Customer ran a connection check from WhatsApp; assistant detected an issue.\n"
        f"Diagnostic: {context}\n"
        f"Source phone: {from_number}"
    )

    try:
        result = billing_client.create_support_ticket(
            client_id=session.client_id,
            category="connectivity",
            subject="Connection check failure",
            message=description,
            source="whatsapp",
            source_phone=from_number,
        )
        if result.get("success"):
            ticket_number = result.get("ticket_number", "")
            confirmation = t(lang, "connection_ticket_created", ticket=ticket_number)
            reply = confirmation + "\n\n" + render_main_menu(client_name, lang)
            session_store.set_menu(from_number, "main_menu")
            logger.info("Connection ticket %s created for %s (client_id=%s, context=%s)",
                        ticket_number, from_number, session.client_id, context)
        else:
            logger.error("Connection ticket creation failed for %s: %s",
                         from_number, result.get("error"))
            reply = t(lang, "support_ticket_failed")
    except Exception as e:
        logger.error("Connection ticket error for %s: %s", from_number, e, exc_info=True)
        reply = t(lang, "support_ticket_error")

    session_store.update_after_reply(from_number, body, reply)
    return reply


async def _handle_unlinked_ticket_description(
    session_store: WhatsAppSessionStore,
    session,
    from_number: str,
    body: str,
    lang: str,
) -> str:
    """User provided the unlinked-ticket description — create the ticket."""
    description = body.strip()
    session_store.clear_unlinked_ticket_state(from_number)

    if not description:
        reply = t(lang, "link_ticket_ask_description")
        session_store.update_after_reply(from_number, body, reply)
        return reply

    try:
        result = billing_client.create_support_ticket(
            client_id=None,
            category="general",
            subject="Unverified WhatsApp request",
            message=description,
            source="whatsapp",
            source_phone=from_number,
        )
        if result.get("success"):
            ticket_number = result.get("ticket_number", "")
            reply = t(lang, "link_ticket_unlinked_created", ticket=ticket_number)
            logger.info("Unlinked ticket %s created for %s", ticket_number, from_number)
        else:
            logger.error("Unlinked ticket creation failed for %s: %s",
                         from_number, result.get("error"))
            reply = t(lang, "support_ticket_failed")
    except Exception as e:
        logger.error("Unlinked ticket error for %s: %s", from_number, e, exc_info=True)
        reply = t(lang, "support_ticket_error")

    session_store.update_after_reply(from_number, body, reply)
    return reply
