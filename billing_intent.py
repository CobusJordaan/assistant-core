"""Detect billing intent from user messages."""

import re


def detect_billing_intent(message: str) -> dict | None:
    """Parse billing-related commands from a message.

    Supported patterns:
        find client <query>
        client balance <client_id>
        client balance              (follow-up, needs session context)
        balance                     (follow-up)
        unpaid invoices <client_id>
        unpaid invoices             (follow-up)
        client summary <client_id>
        client summary              (follow-up)
        summary                     (follow-up)
        use <client_id>             (select client)

    Returns {"tool": "billing_...", "args": {...}} or None.
    Follow-up commands return args without client_id — caller resolves from session.
    """
    text = message.strip()
    lower = text.lower()

    # use <client_id> — select a client
    m = re.match(r"^use\s+(\d+)$", lower)
    if m:
        return {"tool": "billing_select_client", "args": {"client_id": int(m.group(1))}}

    # find client <query>
    m = re.match(r"^find\s+client\s+(.+)$", lower)
    if m:
        query = m.group(1).strip()
        if query:
            return {"tool": "billing_client_lookup", "args": {"query": query}}

    # client balance <client_id>
    m = re.match(r"^client\s+balance\s+(\d+)$", lower)
    if m:
        return {"tool": "billing_client_balance", "args": {"client_id": int(m.group(1))}}

    # balance or client balance (follow-up, no ID)
    if lower in ("balance", "client balance"):
        return {"tool": "billing_client_balance", "args": {}}

    # unpaid invoices <client_id>
    m = re.match(r"^unpaid\s+invoices?\s+(\d+)$", lower)
    if m:
        return {"tool": "billing_unpaid_invoices", "args": {"client_id": int(m.group(1))}}

    # unpaid invoices (follow-up, no ID)
    if lower in ("unpaid invoices", "unpaid invoice"):
        return {"tool": "billing_unpaid_invoices", "args": {}}

    # client summary <client_id>
    m = re.match(r"^client\s+summary\s+(\d+)$", lower)
    if m:
        return {"tool": "billing_client_summary", "args": {"client_id": int(m.group(1))}}

    # summary or client summary (follow-up, no ID)
    if lower in ("summary", "client summary"):
        return {"tool": "billing_client_summary", "args": {}}

    return None
