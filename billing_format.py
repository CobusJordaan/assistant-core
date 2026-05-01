"""Format billing tool results into clean human-readable text."""


def format_billing_result(tool_name: str, result: dict) -> str | None:
    """Format a billing tool result. Returns formatted text or None if not a billing tool."""
    data = _extract_data(result)
    if isinstance(data, str):
        return data  # error message

    formatters = {
        "billing_client_balance": _format_client_balance,
        "billing_unpaid_invoices": _format_unpaid_invoices,
        "billing_client_summary": _format_client_summary,
    }

    fn = formatters.get(tool_name)
    if fn:
        return fn(data)
    return None


def format_radius_status(data: dict) -> str:
    """Format a client RADIUS status result into readable text."""
    if not data.get("success"):
        return f"RADIUS check failed: {data.get('error', 'Unknown error')}"

    name = data.get("client_name", "Unknown")
    num = data.get("client_number", "")
    header = f"{name} ({num})" if num else name

    if not data.get("is_online"):
        reason = data.get("reason", "")
        msg = f"{header} is currently OFFLINE."
        if reason:
            msg += f"\n  Note: {reason}"
        return msg

    ip = data.get("ip_address", "N/A")
    mac = data.get("mac_address", "N/A")
    nas = data.get("nas_ip", "N/A")
    session_start = data.get("session_start", "N/A")
    radius_user = data.get("radius_username") or data.get("logged_in_as", "N/A")

    lines = [f"{header} is ONLINE."]
    lines.append(f"  IP address:    {ip}")
    lines.append(f"  MAC address:   {mac}")
    lines.append(f"  NAS IP:        {nas}")
    lines.append(f"  RADIUS user:   {radius_user}")
    if session_start and session_start != "N/A":
        lines.append(f"  Session start: {session_start}")
    return "\n".join(lines)


def format_client_ping(data: dict) -> str:
    """Format a client ping result into readable text."""
    if not data.get("success"):
        return f"Ping check failed: {data.get('error', 'Unknown error')}"

    name = data.get("client_name", "Unknown")
    num = data.get("client_number", "")
    header = f"{name} ({num})" if num else name

    if not data.get("is_online"):
        return f"{header} is OFFLINE — cannot ping."

    if not data.get("ping"):
        return f"{header} is online but has no IP address — cannot ping."

    ip = data.get("ip_address", "N/A")
    mac = data.get("mac_address", "N/A")
    ping = data.get("ping", {})
    ping_ok = ping.get("success", False)
    ping_output = ping.get("output", "").strip()

    lines = [f"{header} — RADIUS online, IP: {ip}"]
    lines.append(f"  MAC: {mac}")
    lines.append(f"  Ping {ip}: {'REACHABLE' if ping_ok else 'UNREACHABLE'}")
    if ping_output:
        for line in ping_output.splitlines()[-6:]:  # last 6 lines of ping output
            lines.append(f"    {line}")
    return "\n".join(lines)


SHORTLIST_MAX = 5


def format_client_lookup(result: dict, query: str = "") -> tuple[str, list[dict]]:
    """Format client lookup results.

    Returns (formatted_text, clients_list).
    clients_list is the full ranked client list for session selection logic.
    """
    data = _extract_data(result)
    if isinstance(data, str):
        return data, []

    clients = data.get("clients", [])
    total = data.get("count", len(clients))

    if total == 0:
        return "No matching clients found.", []

    # Rank results by relevance to query
    if query:
        clients = _rank_clients(clients, query)

    if len(clients) == 1:
        text = _format_single_client(clients[0])
        return text, clients

    # Multiple matches — capped numbered shortlist
    shown = clients[:SHORTLIST_MAX]
    lines = [f"Found {total} matching client{'s' if total != 1 else ''}:"]
    for i, c in enumerate(shown, 1):
        lines.append(f"  {i}. {_format_client_line(c)}")
    if total > SHORTLIST_MAX:
        lines.append(f"  ... and {total - SHORTLIST_MAX} more.")
    lines.append("")
    lines.append('Type "use <client_id>" to select a client.')

    return "\n".join(lines), clients


def _rank_clients(clients: list[dict], query: str) -> list[dict]:
    """Rank clients by match quality against the search query."""
    q = query.lower().strip()

    def score(c: dict) -> tuple[int, str]:
        name = (c.get("fullname") or "").lower()
        # 0 = exact fullname match
        if name == q:
            return (0, name)
        # 1 = fullname starts with query
        if name.startswith(q):
            return (1, name)
        # 2 = first name exact match (first word)
        first = name.split()[0] if name else ""
        if first == q:
            return (2, name)
        # 3 = first name starts with query
        if first.startswith(q):
            return (3, name)
        # 4 = everything else
        return (4, name)

    return sorted(clients, key=score)


def _extract_data(result: dict) -> dict | str:
    """Extract data from tool result, returning error string on failure."""
    data = result.get("result")
    if not data:
        error = result.get("error") or "No data returned"
        return f"Billing error: {error}"
    if not isinstance(data, dict):
        return f"Billing error: unexpected response"
    if not data.get("success"):
        return f"Billing API error: {data.get('error', 'Unknown error')}"
    return data


def _format_client_line(c: dict) -> str:
    """Format a single client as a one-liner for shortlists."""
    cid = c.get("id", "?")
    name = c.get("fullname", "Unknown")
    num = c.get("client_number", "")
    status = c.get("status", "")
    email = c.get("email", "")
    reseller = c.get("reseller_name", "")

    parts = [str(cid), name]
    if num:
        parts.append(num)
    if status:
        parts.append(status)
    if email:
        parts.append(email)
    if reseller:
        parts.append(f"[{reseller}]")

    return " | ".join(parts)


def _format_single_client(c: dict) -> str:
    """Format a single auto-selected client match."""
    lines = [f"Found 1 match — auto-selected:"]
    lines.append(f"  {_format_client_line(c)}")
    return "\n".join(lines)


def _format_client_balance(data: dict) -> str:
    name = data.get("fullname", "Unknown")
    balance = data.get("account_balance", 0)
    outstanding = data.get("outstanding_invoice_total", 0)
    unpaid_count = data.get("unpaid_invoice_count", 0)
    status = data.get("status", "")

    lines = [f"Client balance for {name}:"]
    lines.append(f"  - Account balance: R{balance:,.2f}")
    lines.append(f"  - Outstanding invoices: R{outstanding:,.2f} ({unpaid_count} unpaid)")
    if status:
        lines.append(f"  - Status: {status}")

    return "\n".join(lines)


def _format_unpaid_invoices(data: dict) -> str:
    name = data.get("fullname", "Unknown")
    invoices = data.get("invoices", [])
    count = data.get("count", len(invoices))
    total = data.get("total_outstanding", 0)

    if count == 0:
        return f"No unpaid invoices for {name}."

    lines = [f"{count} unpaid invoice{'s' if count != 1 else ''} for {name} (total: R{total:,.2f}):"]
    for inv in invoices:
        num = inv.get("invoice_number", "?")
        due = inv.get("due_date", "?")
        balance = inv.get("balance", 0)
        status = inv.get("payment_status", "")

        line = f"  - {num} | Due: {due} | Outstanding: R{balance:,.2f}"
        if status:
            line += f" | {status}"
        lines.append(line)

    return "\n".join(lines)


def _format_client_summary(data: dict) -> str:
    client = data.get("client", {})
    billing = data.get("billing", {})
    services = data.get("services", {})

    name = client.get("fullname", "Unknown")
    num = client.get("client_number", "")
    status = client.get("status", "")
    email = client.get("email", "")
    phone = client.get("phone") or client.get("mobile_number", "")
    reseller = client.get("reseller_name", "")
    payment_method = client.get("payment_method", "")

    lines = [f"Client summary for {name}:"]

    # Basic info
    info_parts = []
    if num:
        info_parts.append(f"#{num}")
    if status:
        info_parts.append(status)
    if reseller:
        info_parts.append(reseller)
    if info_parts:
        lines.append(f"  {' | '.join(info_parts)}")

    if email:
        lines.append(f"  Email: {email}")
    if phone:
        lines.append(f"  Phone: {phone}")
    if payment_method:
        lines.append(f"  Payment method: {payment_method}")

    # Billing
    balance = billing.get("account_balance", 0)
    outstanding = billing.get("outstanding_invoice_total", 0)
    unpaid_count = billing.get("unpaid_invoice_count", 0)
    lines.append("")
    lines.append("Billing:")
    lines.append(f"  - Account balance: R{balance:,.2f}")
    lines.append(f"  - Outstanding: R{outstanding:,.2f} ({unpaid_count} unpaid)")

    # Recent payments
    payments = billing.get("recent_payments", [])
    if payments:
        lines.append(f"  - Last {len(payments)} payment{'s' if len(payments) != 1 else ''}:")
        for p in payments[:5]:
            date = p.get("payment_date", "?")
            amount = p.get("amount", 0)
            method = p.get("payment_method", "")
            ref = p.get("reference", "")
            line = f"      {date} | R{amount:,.2f}"
            if method:
                line += f" | {method}"
            if ref:
                line += f" | {ref}"
            lines.append(line)

    # Services
    pkg = services.get("main_package")
    if pkg:
        lines.append("")
        lines.append("Package:")
        pkg_name = pkg.get("package_name", "?")
        pkg_price = pkg.get("price", 0)
        dl = pkg.get("download_speed", "?")
        ul = pkg.get("upload_speed", "?")
        lines.append(f"  - {pkg_name} | R{pkg_price:,.2f} | {dl}/{ul} Mbps")

    addons = services.get("additional_services", [])
    if addons:
        lines.append(f"  - {len(addons)} additional service{'s' if len(addons) != 1 else ''}:")
        for s in addons:
            sname = s.get("service_name", "?")
            sprice = s.get("price", 0)
            lines.append(f"      {sname} | R{sprice:,.2f}")

    return "\n".join(lines)
