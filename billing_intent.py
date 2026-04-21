"""Detect billing intent from user messages."""

import re


def detect_billing_intent(message: str) -> dict | None:
    """Parse billing-related commands from a message.

    Supported patterns:
        find client <query>
        client balance <client_id>
        unpaid invoices <client_id>
        client summary <client_id>

    Returns {"tool": "billing_...", "args": {...}} or None.
    """
    text = message.strip()
    lower = text.lower()

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

    # unpaid invoices <client_id>
    m = re.match(r"^unpaid\s+invoices?\s+(\d+)$", lower)
    if m:
        return {"tool": "billing_unpaid_invoices", "args": {"client_id": int(m.group(1))}}

    # client summary <client_id>
    m = re.match(r"^client\s+summary\s+(\d+)$", lower)
    if m:
        return {"tool": "billing_client_summary", "args": {"client_id": int(m.group(1))}}

    return None
