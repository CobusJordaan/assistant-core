"""Action router: maps classified WhatsApp intents to CRM/tool calls."""

import logging
from dataclasses import dataclass, field

from billing_client import billing_client
from tools import execute_tool
from whatsapp_intent import WhatsAppIntent

logger = logging.getLogger("assistant-core.wa-actions")


@dataclass
class ActionResult:
    """Structured result from an action execution."""
    action: str
    success: bool
    data: dict | None = None
    error: str | None = None
    needs_client: bool = False
    display_hint: str = "unknown"   # balance, invoices, summary, invoice_link,
                                     # statement_link, latency, support, greeting, unknown, error


FALLBACK_ERROR = "I'm having trouble processing that right now. Please try again in a moment."

_CLIENT_ACTIONS = frozenset({
    "balance_check", "unpaid_invoices", "client_summary",
    "send_invoice_link", "send_statement_link",
})

_HINT_MAP = {
    "balance_check": "balance",
    "unpaid_invoices": "invoices",
    "client_summary": "summary",
    "send_invoice_link": "invoice_link",
    "send_statement_link": "statement_link",
}


async def execute_action(intent: WhatsAppIntent, client_id: int | None,
                         client_name: str = "") -> ActionResult:
    """Execute the CRM/tool action for a classified intent."""
    action = intent.action

    logger.info(
        "WA action: action=%s client_id=%s confidence=%.2f",
        action, client_id, intent.confidence,
    )

    try:
        # --- Client-required actions ---
        if action in _CLIENT_ACTIONS:
            if not client_id:
                return ActionResult(
                    action=action, success=False, error="no_client",
                    needs_client=True, display_hint=_HINT_MAP.get(action, "unknown"),
                )
            return await _execute_client_action(action, client_id)

        # --- Latency check ---
        if action == "latency_check":
            return await _execute_latency_check(intent.entities)

        # --- Support intake (NL match → show support menu) ---
        if action == "support_intake":
            return ActionResult(
                action=action, success=True,
                data={"message": intent.raw_message},
                display_hint="support_menu",
            )

        # --- Greeting ---
        if action == "greeting":
            return ActionResult(
                action=action, success=True, data={}, display_hint="greeting",
            )

        # --- Unknown ---
        return ActionResult(action="unknown", success=True, data={}, display_hint="unknown")

    except Exception as e:
        logger.error("Action error: action=%s error=%s", action, e, exc_info=True)
        return ActionResult(
            action=action, success=False, error=str(e), display_hint="error",
        )


# ---------------------------------------------------------------------------
# Client actions
# ---------------------------------------------------------------------------

async def _execute_client_action(action: str, client_id: int) -> ActionResult:
    """Run a billing API call that requires client context."""

    if action == "balance_check":
        result = billing_client.client_balance(client_id)
        return ActionResult(
            action=action, success=result.get("success", False),
            data=result, error=result.get("error"),
            display_hint="balance",
        )

    if action == "unpaid_invoices":
        result = billing_client.client_unpaid_invoices(client_id, limit=5)
        return ActionResult(
            action=action, success=result.get("success", False),
            data=result, error=result.get("error"),
            display_hint="invoices",
        )

    if action == "client_summary":
        result = billing_client.client_summary(client_id)
        return ActionResult(
            action=action, success=result.get("success", False),
            data=result, error=result.get("error"),
            display_hint="summary",
        )

    # Interim: fetch info only; actual send requires billing-side endpoints
    if action == "send_invoice_link":
        result = billing_client.client_unpaid_invoices(client_id, limit=1)
        return ActionResult(
            action=action, success=True, data=result,
            display_hint="invoice_link",
        )

    if action == "send_statement_link":
        result = billing_client.client_balance(client_id)
        return ActionResult(
            action=action, success=True, data=result,
            display_hint="statement_link",
        )

    return ActionResult(action=action, success=False, error="unknown_client_action", display_hint="error")


# ---------------------------------------------------------------------------
# Network tools
# ---------------------------------------------------------------------------

async def _execute_latency_check(entities: dict) -> ActionResult:
    """Run a ping check using the existing tool registry."""
    host = entities.get("host", "8.8.8.8")
    result = await execute_tool("ping", {"host": host, "count": 3})
    inner = result.get("result", {})
    return ActionResult(
        action="latency_check",
        success=inner.get("success", False),
        data=result,
        error=result.get("error"),
        display_hint="latency",
    )
