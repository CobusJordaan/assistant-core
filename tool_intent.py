"""Detect tool intent from user messages."""

import re
from billing_intent import detect_billing_intent


def detect_intent(message: str) -> dict | None:
    """Detect if a message is requesting a tool.

    Returns {"tool": "...", "args": {...}} or None.
    """
    text = message.strip().lower()

    # --- Billing intents (checked first for specificity) ---
    billing = detect_billing_intent(message)
    if billing:
        return billing

    # --- Built-in network tools ---

    # ping <host>
    m = re.match(r"^ping\s+(\S+)(?:\s+(\d+))?$", text)
    if m:
        args = {"host": m.group(1)}
        if m.group(2):
            args["count"] = int(m.group(2))
        return {"tool": "ping", "args": args}

    # dns[_ ]lookup <hostname> | nslookup <hostname> | resolve <hostname>
    m = re.match(r"^(?:dns[_ ]?lookup|nslookup|resolve)\s+(\S+)$", text)
    if m:
        return {"tool": "dns_lookup", "args": {"hostname": m.group(1)}}

    # http[_ ]check <url> | check url <url>
    m = re.match(r"^(?:http[_ ]?check|check\s+url)\s+(\S+)$", text)
    if m:
        url = m.group(1)
        if not url.startswith("http"):
            url = "https://" + url
        return {"tool": "http_check", "args": {"url": url}}

    # tcp[_ ]check <host> <port> | check port <host> <port>
    m = re.match(r"^(?:tcp[_ ]?check|check\s+port)\s+(\S+)\s+(\d+)$", text)
    if m:
        return {"tool": "tcp_check", "args": {"host": m.group(1), "port": int(m.group(2))}}

    return None
