"""Format ActionResult into short, natural WhatsApp replies (bilingual EN/AF)."""

import re
from whatsapp_actions import ActionResult
from whatsapp_i18n import t


def format_wa_greeting(client_name: str = "", lang: str = "en") -> str:
    """First-message greeting for a new session."""
    first = client_name.strip().split()[0] if client_name.strip() else ""
    if first:
        return t(lang, "main_menu_greeting", name=first)
    return t(lang, "main_menu_greeting_anon")


def format_wa_reply(result: ActionResult, client_name: str = "", lang: str = "en") -> str:
    """Format an ActionResult into a WhatsApp-friendly reply."""
    first_name = client_name.strip().split()[0] if client_name.strip() else ""

    # No client matched
    if result.needs_client:
        return t(lang, "needs_client")

    # Action failed
    if not result.success and result.error and result.error != "no_client":
        return t(lang, "generic_error")

    hint = result.display_hint

    if hint == "greeting":
        return format_wa_greeting(client_name, lang)

    if hint == "balance":
        return _format_balance(result.data, first_name, lang)

    if hint == "invoices":
        return _format_invoices(result.data, lang)

    if hint == "summary":
        return _format_summary(result.data, lang)

    if hint == "invoice_link":
        return _format_invoice_link(result.data, lang)

    if hint == "statement_link":
        return _format_statement_link(result.data, lang)

    if hint == "latency":
        return _format_latency(result.data, lang)

    if hint == "support":
        return t(lang, "support_ack")

    # Unknown intent fallback
    return t(lang, "unknown_fallback")


# ---------------------------------------------------------------------------
# Per-action formatters
# ---------------------------------------------------------------------------

def _format_balance(data: dict, first_name: str, lang: str) -> str:
    balance = data.get("account_balance", 0)
    outstanding = data.get("outstanding_invoice_total", 0)
    unpaid_count = data.get("unpaid_invoice_count", 0)

    if first_name:
        name_part = t(lang, "balance_name_prefix", first_name=first_name)
    else:
        name_part = t(lang, "balance_anon_prefix")

    lines = [t(lang, "balance_line", name=name_part, balance=f"{balance:,.2f}")]
    if outstanding > 0:
        lines.append(t(lang, "outstanding_line", amount=f"{outstanding:,.2f}", count=unpaid_count))
    else:
        lines.append(t(lang, "no_outstanding"))
    return "\n".join(lines)


def _format_invoices(data: dict, lang: str) -> str:
    invoices = data.get("invoices", [])
    total = data.get("total_outstanding", 0)
    count = data.get("count", len(invoices))

    if count == 0:
        return t(lang, "no_unpaid_invoices")

    s = "s" if count != 1 else ""
    re_suffix = "re" if count != 1 else "ur"
    lines = [t(lang, "unpaid_invoices_header", count=count, s=s, re=re_suffix, total=f"{total:,.2f}")]
    for inv in invoices[:5]:
        num = inv.get("invoice_number", "?")
        due = inv.get("due_date", "?")
        bal = inv.get("balance", 0)
        lines.append(f"\u2022 {num} \u2014 due {due} \u2014 *R{bal:,.2f}*")
    if count > 5:
        lines.append(t(lang, "invoices_more", n=count - 5))
    return "\n".join(lines)


def _format_summary(data: dict, lang: str) -> str:
    client = data.get("client", {})
    billing = data.get("billing", {})
    services = data.get("services", {})

    name = client.get("fullname", "")
    balance = billing.get("account_balance", 0)
    outstanding = billing.get("outstanding_invoice_total", 0)
    pkg = services.get("main_package")

    lines = [t(lang, "summary_header", name=name)]
    lines.append(t(lang, "summary_balance", balance=f"{balance:,.2f}"))
    lines.append(t(lang, "summary_outstanding", amount=f"{outstanding:,.2f}"))
    if pkg:
        pkg_name = pkg.get("package_name", "")
        dl = pkg.get("download_speed", "?")
        ul = pkg.get("upload_speed", "?")
        lines.append(f"Package: {pkg_name} ({dl}/{ul} Mbps)")
    return "\n".join(lines)


def _format_invoice_link(data: dict, lang: str) -> str:
    invoices = data.get("invoices", [])
    if invoices:
        inv = invoices[0]
        num = inv.get("invoice_number", "?")
        bal = inv.get("balance", 0)
        return t(lang, "invoice_link_found", num=num, amount=f"{bal:,.2f}")
    return t(lang, "invoice_link_none")


def _format_statement_link(data: dict, lang: str) -> str:
    balance = data.get("account_balance", 0)
    return t(lang, "statement_link", balance=f"{balance:,.2f}")


def _format_latency(data: dict, lang: str) -> str:
    result = data.get("result", {})
    host = result.get("host", "the target")
    if result.get("success"):
        output = result.get("output", "")
        avg_match = re.search(r"Average\s*=\s*(\d+)ms|avg[^=]*=\s*([\d.]+)", output)
        if avg_match:
            avg = avg_match.group(1) or avg_match.group(2)
            return t(lang, "latency_ok_avg", host=host, avg=avg)
        return t(lang, "latency_ok", host=host)
    return t(lang, "latency_fail", host=host)
