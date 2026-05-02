"""Action router: maps classified WhatsApp intents to CRM/tool calls."""

import logging
from dataclasses import dataclass, field

from billing_client import billing_client
from radius_tools import tool_client_ping
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
    "connection_check",
})

_HINT_MAP = {
    "balance_check": "balance",
    "unpaid_invoices": "invoices",
    "client_summary": "summary",
    "send_invoice_link": "invoice_link",
    "send_statement_link": "statement_link",
    "connection_check": "connection",
}


async def execute_action(intent: WhatsAppIntent, client_id: int | None,
                         client_name: str = "",
                         from_number: str = "",
                         lang: str = "en") -> ActionResult:
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
            return await _execute_client_action(action, client_id, from_number, lang)

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

async def _execute_client_action(action: str, client_id: int,
                                 from_number: str = "",
                                 lang: str = "en") -> ActionResult:
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

    if action == "send_invoice_link":
        try:
            result = billing_client.send_invoice_whatsapp(client_id, phone_number=from_number, language=lang)
            # "no_unpaid_invoices" is not a failure — it's valid info
            is_ok = result.get("success", False) or result.get("error") == "no_unpaid_invoices"
            return ActionResult(
                action=action, success=is_ok,
                data=result, error=result.get("error"),
                display_hint="invoice_link",
            )
        except Exception as e:
            logger.error("Send invoice WhatsApp failed: %s", e)
            return ActionResult(
                action=action, success=False,
                error="send_failed", display_hint="invoice_link",
            )

    if action == "send_statement_link":
        try:
            result = billing_client.send_statement_whatsapp(client_id, phone_number=from_number, language=lang)
            return ActionResult(
                action=action, success=result.get("success", False),
                data=result, error=result.get("error"),
                display_hint="statement_link",
            )
        except Exception as e:
            logger.error("Send statement WhatsApp failed: %s", e)
            return ActionResult(
                action=action, success=False,
                error="send_failed", display_hint="statement_link",
            )

    if action == "connection_check":
        return await _execute_connection_check(client_id)

    return ActionResult(action=action, success=False, error="unknown_client_action", display_hint="error")


# ---------------------------------------------------------------------------
# Connection check (RADIUS lookup + ping the client's IP)
# ---------------------------------------------------------------------------

async def _execute_connection_check(client_id: int) -> ActionResult:
    """Fetch the client's RADIUS session and ping their IP. Mirrors the
    crm_tools.py `ping_client_ip` flow: online + ping reachable means healthy;
    anything else (offline, no IP, ping fail) signals an outage and the caller
    should offer a support ticket as the follow-up step."""
    try:
        result = tool_client_ping(client_id)
    except Exception as e:
        logger.error("connection_check tool error: %s", e, exc_info=True)
        return ActionResult(
            action="connection_check", success=False,
            error=str(e), display_hint="connection",
        )

    if not result.get("success"):
        return ActionResult(
            action="connection_check", success=False,
            data=result, error=result.get("error", "connection_check_failed"),
            display_hint="connection",
        )

    # Healthy = online AND ping reachable. Everything else triggers the
    # ticket-offer path in the formatter/handler.
    ping = result.get("ping") or {}
    healthy = bool(result.get("is_online")) and bool(ping.get("success"))

    return ActionResult(
        action="connection_check", success=True,
        data={**result, "healthy": healthy, "needs_ticket_offer": not healthy},
        display_hint="connection",
    )


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
