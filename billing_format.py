"""Format billing tool results into clean human-readable text."""


def format_billing_result(tool_name: str, result: dict) -> str | None:
    """Format a billing tool result. Returns formatted text or None if not a billing tool."""
    data = result.get("result")
    if not data:
        error = result.get("error") or data
        return f"Billing error: {error}"

    if not isinstance(data, dict):
        return None

    # API-level error
    if not data.get("success"):
        return f"Billing API error: {data.get('error', 'Unknown error')}"

    formatters = {
        "billing_client_lookup": _format_client_lookup,
        "billing_client_balance": _format_client_balance,
        "billing_unpaid_invoices": _format_unpaid_invoices,
        "billing_client_summary": _format_client_summary,
    }

    fn = formatters.get(tool_name)
    if fn:
        return fn(data)
    return None


def _format_client_lookup(data: dict) -> str:
    clients = data.get("clients", [])
    count = data.get("count", len(clients))

    if count == 0:
        return "No matching clients found."

    lines = [f"Found {count} matching client{'s' if count != 1 else ''}:"]
    for c in clients:
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

        lines.append(f"  - {' | '.join(parts)}")

    return "\n".join(lines)


def _format_client_balance(data: dict) -> str:
    name = data.get("fullname", "Unknown")
    balance = data.get("account_balance", 0)
    outstanding = data.get("outstanding_invoice_balance", 0)
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
    balance = client.get("account_balance", 0)
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
    outstanding = billing.get("outstanding_balance", 0)
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
