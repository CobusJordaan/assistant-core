"""Detect billing intent from user messages."""

import re

# Client number pattern: 2-6 uppercase letters followed by 3-7 digits (e.g. DRA0011, WN0042)
_CLIENT_NUM_RE = re.compile(r'\b([A-Z]{2,6}\d{3,7})\b')

# Keywords that indicate a network/connectivity check is needed
_NETWORK_KEYWORDS = re.compile(
    r'\b(troubleshoot|network\s+issue|not\s+online|offline|connectivity|connection\s+problem|'
    r'not\s+connect(?:ing)?|no\s+internet|no\s+connect(?:ion)?|connection|online|ping|'
    r'check\s+(?:connection|network|internet|status)|network\s+check|diagnos|'
    r'is\s+(?:he|she|it|the\s+client)\s+online|not\s+working)\b',
    re.IGNORECASE
)


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

    # current client / who is selected
    if lower in ("current client", "who is selected", "selected client"):
        return {"tool": "billing_current_client", "args": {}}

    # clear client / forget client / deselect
    if lower in ("clear client", "forget client", "forget selected client", "deselect client", "deselect"):
        return {"tool": "billing_clear_client", "args": {}}

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

    # is client <id> online / client <id> online / check client <id> connection
    m = re.match(r"^(?:is\s+)?client\s+(\d+)\s+online$", lower)
    if m:
        return {"tool": "client_radius_status", "args": {"client_id": int(m.group(1))}}

    m = re.match(r"^check\s+client\s+(\d+)\s+(?:connection|status|online)$", lower)
    if m:
        return {"tool": "client_radius_status", "args": {"client_id": int(m.group(1))}}

    # online / connection status (follow-up, no ID)
    if lower in ("online", "is online", "connection", "connection status", "online status", "check connection"):
        return {"tool": "client_radius_status", "args": {}}

    # ping client <id>
    m = re.match(r"^ping\s+client\s+(\d+)$", lower)
    if m:
        return {"tool": "client_ping", "args": {"client_id": int(m.group(1))}}

    # ping client (follow-up, no ID)
    if lower in ("ping client", "ping"):
        return {"tool": "client_ping", "args": {}}

    # Natural language network check — client number + any network keyword
    # e.g. "troubleshoot network issue for DRA0011", "DRA0011 is offline", "ping DRA0011"
    client_num_match = _CLIENT_NUM_RE.search(message)
    if client_num_match and _NETWORK_KEYWORDS.search(message):
        return {"tool": "client_network_check", "args": {"client_number": client_num_match.group(1)}}

    # Client number alone — treat as a ping/connection check shorthand
    # e.g. "DRA0011 online?" or just "DRA0011" as a follow-up after a network discussion
    if client_num_match and lower.strip() == client_num_match.group(1).lower():
        return {"tool": "client_network_check", "args": {"client_number": client_num_match.group(1)}}

    # Network check follow-up (no client number — use session context)
    if _NETWORK_KEYWORDS.search(message) and not client_num_match:
        if lower.strip() in ("troubleshoot", "network issue", "not online", "offline",
                             "check connection", "check network", "diagnose", "network check"):
            return {"tool": "client_network_check", "args": {}}

    return None
