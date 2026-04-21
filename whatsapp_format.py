"""Format ActionResult into short, natural WhatsApp replies."""

import re
from whatsapp_actions import ActionResult


def format_wa_greeting(client_name: str = "") -> str:
    """First-message greeting for a new session."""
    first = client_name.strip().split()[0] if client_name.strip() else ""
    if first:
        return f"Hi {first} \U0001f44b How can I help you today?"
    return "Hi \U0001f44b How can I help you today?"


def format_wa_reply(result: ActionResult, client_name: str = "") -> str:
    """Format an ActionResult into a WhatsApp-friendly reply."""
    first_name = client_name.strip().split()[0] if client_name.strip() else ""

    # No client matched
    if result.needs_client:
        return (
            "I couldn't match your number to an account on our system. "
            "Could you please share your account number or the name on your account?"
        )

    # Action failed
    if not result.success and result.error and result.error != "no_client":
        return "Sorry, something went wrong on my side. Please try again in a moment."

    hint = result.display_hint

    if hint == "greeting":
        return format_wa_greeting(client_name)

    if hint == "balance":
        return _format_balance(result.data, first_name)

    if hint == "invoices":
        return _format_invoices(result.data)

    if hint == "summary":
        return _format_summary(result.data)

    if hint == "invoice_link":
        return _format_invoice_link(result.data)

    if hint == "statement_link":
        return _format_statement_link(result.data)

    if hint == "latency":
        return _format_latency(result.data)

    if hint == "support":
        return (
            "I'm sorry to hear you're having trouble. "
            "I've noted your message and our support team will follow up shortly. "
            "Is there anything else I can check for you, like your balance or invoices?"
        )

    # Unknown intent
    return (
        "I'm not sure I understand. I can help with:\n"
        "\u2022 Account balance\n"
        "\u2022 Unpaid invoices\n"
        "\u2022 Account summary\n"
        "\u2022 Invoice or statement requests\n"
        "\u2022 Connection issues\n\n"
        "What would you like to know?"
    )


# ---------------------------------------------------------------------------
# Per-action formatters
# ---------------------------------------------------------------------------

def _format_balance(data: dict, first_name: str) -> str:
    balance = data.get("account_balance", 0)
    outstanding = data.get("outstanding_invoice_balance", 0)
    unpaid_count = data.get("unpaid_invoice_count", 0)
    name_part = f"{first_name}, your" if first_name else "Your"

    lines = [f"{name_part} account balance is *R{balance:,.2f}*."]
    if outstanding > 0:
        lines.append(f"Outstanding invoices: *R{outstanding:,.2f}* ({unpaid_count} unpaid).")
    else:
        lines.append("You have no outstanding invoices \u2705")
    return "\n".join(lines)


def _format_invoices(data: dict) -> str:
    invoices = data.get("invoices", [])
    total = data.get("total_outstanding", 0)
    count = data.get("count", len(invoices))

    if count == 0:
        return "Great news \u2014 you have no unpaid invoices! \u2705"

    lines = [
        f"You have *{count}* unpaid invoice{'s' if count != 1 else ''} "
        f"totalling *R{total:,.2f}*:"
    ]
    for inv in invoices[:5]:
        num = inv.get("invoice_number", "?")
        due = inv.get("due_date", "?")
        bal = inv.get("balance", 0)
        lines.append(f"\u2022 {num} \u2014 due {due} \u2014 *R{bal:,.2f}*")
    if count > 5:
        lines.append(f"...and {count - 5} more.")
    return "\n".join(lines)


def _format_summary(data: dict) -> str:
    client = data.get("client", {})
    billing = data.get("billing", {})
    services = data.get("services", {})

    name = client.get("fullname", "")
    balance = client.get("account_balance", 0)
    outstanding = billing.get("outstanding_balance", 0)
    pkg = services.get("main_package")

    lines = [f"*Account summary for {name}*"]
    lines.append(f"Balance: *R{balance:,.2f}*")
    lines.append(f"Outstanding: *R{outstanding:,.2f}*")
    if pkg:
        pkg_name = pkg.get("package_name", "")
        dl = pkg.get("download_speed", "?")
        ul = pkg.get("upload_speed", "?")
        lines.append(f"Package: {pkg_name} ({dl}/{ul} Mbps)")
    return "\n".join(lines)


def _format_invoice_link(data: dict) -> str:
    """Interim: show latest invoice info. Actual link sending needs billing-side endpoints."""
    invoices = data.get("invoices", [])
    if invoices:
        inv = invoices[0]
        num = inv.get("invoice_number", "?")
        bal = inv.get("balance", 0)
        return (
            f"Your latest unpaid invoice is *{num}* for *R{bal:,.2f}*.\n"
            "I'll have our team send you the invoice link shortly."
        )
    return (
        "I couldn't find any recent invoices on your account. "
        "Would you like me to check something else?"
    )


def _format_statement_link(data: dict) -> str:
    """Interim: acknowledge request. Actual link sending needs billing-side endpoints."""
    balance = data.get("account_balance", 0)
    return (
        f"Your current balance is *R{balance:,.2f}*.\n"
        "I'll have our team send you a statement link shortly."
    )


def _format_latency(data: dict) -> str:
    result = data.get("result", {})
    host = result.get("host", "the target")
    if result.get("success"):
        output = result.get("output", "")
        avg_match = re.search(r"Average\s*=\s*(\d+)ms|avg[^=]*=\s*([\d.]+)", output)
        if avg_match:
            avg = avg_match.group(1) or avg_match.group(2)
            return f"Ping to {host}: *{avg}ms* average. Connection looks OK from our side."
        return f"Ping to {host} was successful. Connection seems fine from our end."
    return (
        f"I couldn't reach {host} from our side either. "
        "This might indicate a wider issue. Our support team will investigate."
    )
