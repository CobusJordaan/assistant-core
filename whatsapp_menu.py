"""Numbered menu system for WhatsApp assistant.

Manages menu definitions, rendering, and numeric reply resolution.
Menus are stored in session state and resolved before intent classification.
"""

import logging
from datetime import datetime, timezone, timedelta

from whatsapp_i18n import t

logger = logging.getLogger("assistant-core.wa-menu")

# Menu timeout — same as session timeout
MENU_TIMEOUT_MINUTES = 30

# ---------------------------------------------------------------------------
# Menu definitions
# ---------------------------------------------------------------------------

MAIN_MENU = {
    "menu_key": "main_menu",
    "options": {
        "1": {"label": "Account summary", "action": "client_summary"},
        "2": {"label": "Balance and unpaid invoices", "action": "balance_check"},
        "3": {"label": "Invoice or statement", "action": "_document_menu"},
        "4": {"label": "Support", "action": "_support_menu"},
        "5": {"label": "Connection check", "action": "connection_check"},
        "6": {"label": "New connection / extra service", "action": "_new_connection"},
    },
}

DOCUMENT_MENU = {
    "menu_key": "document_menu",
    "options": {
        "1": {"label": "Latest invoice", "action": "send_invoice_link"},
        "2": {"label": "Latest statement", "action": "send_statement_link"},
    },
}

SUPPORT_MENU = {
    "menu_key": "support_menu",
    "options": {
        "1": {"label": "Connectivity / speed issue", "action": "_support_connectivity"},
        "2": {"label": "Billing or payment query", "action": "_support_billing"},
        "3": {"label": "General / other", "action": "_support_other"},
    },
}

# Category label lookup for ticket creation
SUPPORT_CATEGORIES = {
    "_support_connectivity": {"key": "connectivity", "label": "Connectivity / speed issue"},
    "_support_billing": {"key": "billing", "label": "Billing or payment query"},
    "_support_other": {"key": "general", "label": "General / other"},
}

_MENU_REGISTRY = {
    "main_menu": MAIN_MENU,
    "document_menu": DOCUMENT_MENU,
    "support_menu": SUPPORT_MENU,
}


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

# i18n label keys per menu
_MENU_LABEL_KEYS = {
    "main_menu": {
        "1": "main_menu_1", "2": "main_menu_2", "3": "main_menu_3",
        "4": "main_menu_4", "5": "main_menu_5", "6": "main_menu_6",
    },
    "document_menu": {
        "1": "document_menu_1", "2": "document_menu_2",
    },
    "support_menu": {
        "1": "support_menu_1", "2": "support_menu_2", "3": "support_menu_3",
    },
}


def render_menu(menu_key: str, preamble: str = "", lang: str = "en") -> str:
    """Render a numbered menu as WhatsApp-friendly text.

    Returns the preamble (if any) followed by numbered options.
    """
    menu = _MENU_REGISTRY.get(menu_key)
    if not menu:
        return preamble

    label_keys = _MENU_LABEL_KEYS.get(menu_key, {})

    lines = []
    if preamble:
        lines.append(preamble)
        lines.append("")
    for num, opt in menu["options"].items():
        label = t(lang, label_keys[num]) if num in label_keys else opt["label"]
        lines.append(f"*{num}.* {label}")
    return "\n".join(lines)


def render_main_menu(client_name: str = "", lang: str = "en") -> str:
    """Render the main greeting menu with personalised preamble."""
    first = client_name.strip().split()[0] if client_name.strip() else ""
    if first:
        preamble = t(lang, "main_menu_greeting", name=first)
    else:
        preamble = t(lang, "main_menu_greeting_anon")
    return render_menu("main_menu", preamble, lang)


def render_invalid_selection(menu_key: str, lang: str = "en") -> str:
    """Render a 'didn't recognise that' message with the current menu."""
    return render_menu(menu_key, t(lang, "invalid_selection"), lang)


def render_document_menu(lang: str = "en") -> str:
    """Render the document sub-menu."""
    return render_menu("document_menu", t(lang, "document_menu_preamble"), lang)


def render_support_menu(lang: str = "en") -> str:
    """Render the support category sub-menu."""
    return render_menu("support_menu", t(lang, "support_menu_preamble"), lang)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def resolve_menu_selection(body: str, menu_key: str | None,
                           menu_created_at: str | None) -> dict | None:
    """Try to resolve a numeric reply against the active menu.

    Returns:
        dict with keys: action, label, menu_key, is_submenu
        or None if body is not a valid menu selection.
    """
    if not menu_key or not body:
        return None

    text = body.strip()

    # Only match single digit (or two digits for future expansion)
    if not text.isdigit() or len(text) > 2:
        return None

    # Check menu timeout
    if menu_created_at:
        try:
            created = datetime.fromisoformat(menu_created_at)
            if (datetime.now(timezone.utc) - created) > timedelta(minutes=MENU_TIMEOUT_MINUTES):
                logger.info("Menu %s expired, ignoring numeric reply '%s'", menu_key, text)
                return None
        except ValueError:
            pass

    menu = _MENU_REGISTRY.get(menu_key)
    if not menu:
        return None

    opt = menu["options"].get(text)
    if not opt:
        logger.info("Invalid menu selection '%s' for menu %s", text, menu_key)
        return {
            "action": "_invalid_selection",
            "label": None,
            "menu_key": menu_key,
            "is_submenu": False,
        }

    action = opt["action"]
    is_submenu = action.startswith("_") and action != "_invalid_selection"

    logger.info("Menu selection: '%s' -> %s (%s) from menu %s",
                text, action, opt["label"], menu_key)

    return {
        "action": action,
        "label": opt["label"],
        "menu_key": menu_key,
        "is_submenu": is_submenu,
    }
