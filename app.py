"""assistant-core — AI assistant backend with Ollama-compatible API."""

import os
import json
import time
import hashlib
import logging
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request
from fastapi.responses import Response
from pydantic import BaseModel
from typing import Any

# Load .env before anything reads os.getenv
load_dotenv()

from memory_api import MemoryStore, memory_router
from tools import register_tool, list_tools, execute_tool
from tool_intent import detect_intent
from billing_client import billing_client
from billing_format import format_billing_result, format_client_lookup
from billing_session import set_client, get_client, clear_client, set_last_lookup, get_last_lookup
from whatsapp import WhatsAppDedup, normalize_phone, twiml_reply, twiml_empty

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "assistant-core")
DATABASE_PATH = os.getenv("DATABASE_PATH", "memory.db")

logger = logging.getLogger("assistant-core")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


# ---------------------------------------------------------------------------
# Billing tool wrappers (registered into the tool registry)
# ---------------------------------------------------------------------------

def _billing_client_lookup(query: str, limit: int = 10) -> dict:
    return billing_client.client_lookup(query, limit)

def _billing_client_balance(client_id: int) -> dict:
    return billing_client.client_balance(client_id)

def _billing_unpaid_invoices(client_id: int, limit: int = 20) -> dict:
    return billing_client.client_unpaid_invoices(client_id, limit)

def _billing_client_summary(client_id: int) -> dict:
    return billing_client.client_summary(client_id)


def _register_billing_tools():
    """Register billing tools if billing API is configured."""
    if not billing_client.configured:
        logger.warning("Billing API not configured — billing tools disabled")
        return

    register_tool(
        "billing_client_lookup", _billing_client_lookup,
        "Search clients by name, email, phone, or client number",
        {"query": {"type": "string", "required": True}, "limit": {"type": "integer", "default": 10}},
    )
    register_tool(
        "billing_client_balance", _billing_client_balance,
        "Get client account balance and outstanding invoice total",
        {"client_id": {"type": "integer", "required": True}},
    )
    register_tool(
        "billing_unpaid_invoices", _billing_unpaid_invoices,
        "List unpaid/partially paid invoices for a client",
        {"client_id": {"type": "integer", "required": True}, "limit": {"type": "integer", "default": 20}},
    )
    register_tool(
        "billing_client_summary", _billing_client_summary,
        "Full client overview: info, billing, services",
        {"client_id": {"type": "integer", "required": True}},
    )
    logger.info("Billing tools registered")


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(application: FastAPI):
    # Startup
    store = MemoryStore(DATABASE_PATH)
    store.initialize()
    application.state.memory = store

    dedup = WhatsAppDedup(DATABASE_PATH)
    dedup.initialize()
    application.state.wa_dedup = dedup

    _register_billing_tools()
    logger.info(f"assistant-core started — {len(list_tools())} tools loaded")

    yield

    # Shutdown
    dedup.close()
    store.close()


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="assistant-core", version="1.0.0", lifespan=lifespan)

# Mount memory routes
app.include_router(memory_router)


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str
    model: str | None = None
    session_id: str | None = None


class ToolRequest(BaseModel):
    tool: str
    args: dict[str, Any] = {}


class OllamaChatRequest(BaseModel):
    model_config = {"extra": "allow"}
    model: str | None = None
    messages: list[dict] = []
    stream: bool = False
    options: dict | None = None
    chat_id: str | None = None


class OllamaGenerateRequest(BaseModel):
    model_config = {"extra": "allow"}
    model: str | None = None
    prompt: str = ""
    stream: bool = False
    options: dict | None = None
    chat_id: str | None = None


# ---------------------------------------------------------------------------
# Shim helpers
# ---------------------------------------------------------------------------

MODEL_NAME = "assistant-core"


def _ollama_chat_response(content: str, model: str | None = None) -> dict:
    """Build an Ollama-compatible /api/chat response."""
    return {
        "model": model or MODEL_NAME,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
        "message": {"role": "assistant", "content": content},
        "done": True,
        "total_duration": 0,
        "load_duration": 0,
        "prompt_eval_count": 0,
        "eval_count": 0,
    }


def _ollama_generate_response(content: str, model: str | None = None) -> dict:
    """Build an Ollama-compatible /api/generate response."""
    return {
        "model": model or MODEL_NAME,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
        "response": content,
        "done": True,
        "total_duration": 0,
        "load_duration": 0,
        "prompt_eval_count": 0,
        "eval_count": 0,
    }


NO_CLIENT_MSG = "No client selected yet. Please run 'find client <name>' first or use 'use <client_id>'."


def _resolve_session_id(request: Request, messages: list[dict] | None = None,
                        chat_id: str | None = None) -> str:
    """Derive a stable session ID from the request context.

    Priority:
    1. X-Session-Id header (explicit)
    2. chat_id from request body (Open WebUI sends this)
    3. Hash of first user message content (stable per conversation)
    4. "default" fallback
    """
    # 1. Explicit header
    header = request.headers.get("X-Session-Id")
    if header:
        return header

    # 2. chat_id from body
    if chat_id:
        return f"chat-{chat_id}"

    # 3. Derive from first user message
    if messages:
        for msg in messages:
            if msg.get("role") == "user" and msg.get("content"):
                digest = hashlib.sha256(msg["content"].encode()).hexdigest()[:16]
                return f"conv-{digest}"

    # 4. Fallback
    return "default"


async def _handle_message(message: str, session_id: str = "default", model: str | None = None) -> tuple[str, str | None]:
    """Process a message through tool detection with billing session context.

    Returns (response_text, tool_name_or_none).
    """
    intent = detect_intent(message)
    if not intent:
        return f"Received: {message}", None

    tool_name = intent["tool"]
    args = intent.get("args", {})

    # --- Billing current client ---
    if tool_name == "billing_current_client":
        ctx = get_client(session_id)
        if ctx:
            return f"Currently selected: client {ctx['client_id']} — {ctx['client_name']}.", tool_name
        return "No client selected yet. Please run 'find client <name>' first.", tool_name

    # --- Billing clear client ---
    if tool_name == "billing_clear_client":
        ctx = get_client(session_id)
        if ctx:
            clear_client(session_id)
            return f"Cleared selected client ({ctx['client_id']} — {ctx['client_name']}).", tool_name
        return "No client was selected.", tool_name

    # --- Billing select client (from last lookup, no API call) ---
    if tool_name == "billing_select_client":
        client_id = args["client_id"]
        last = get_last_lookup(session_id)
        if not last:
            return "No recent client lookup found. Please run 'find client <name>' first.", tool_name
        match = next((c for c in last if c.get("id") == client_id), None)
        if match:
            name = match.get("fullname", "Unknown")
            set_client(session_id, client_id, name)
            return f"Selected client {client_id} — {name}.", tool_name
        else:
            ids = [str(c.get("id", "?")) for c in last]
            return f"Client {client_id} not found in last results. Available IDs: {', '.join(ids)}", tool_name

    # --- Billing client lookup ---
    if tool_name == "billing_client_lookup":
        result = await execute_tool(tool_name, args)
        text, clients = format_client_lookup(result, query=args.get("query", ""))
        # Store results for "use <id>" follow-up
        set_last_lookup(session_id, clients)
        # Auto-select if exactly 1 match
        if len(clients) == 1:
            c = clients[0]
            set_client(session_id, c["id"], c.get("fullname", ""))
        return text, tool_name

    # --- Billing follow-up commands (may need session context) ---
    if tool_name in ("billing_client_balance", "billing_unpaid_invoices", "billing_client_summary"):
        if "client_id" not in args or not args["client_id"]:
            ctx = get_client(session_id)
            if not ctx:
                return NO_CLIENT_MSG, tool_name
            args["client_id"] = ctx["client_id"]

        result = await execute_tool(tool_name, args)
        formatted = format_billing_result(tool_name, result)
        if formatted:
            return formatted, tool_name
        return json.dumps(result, indent=2, default=str), tool_name

    # --- Non-billing tools (ping, dns, http, tcp) ---
    result = await execute_tool(tool_name, args)
    return json.dumps(result, indent=2, default=str), tool_name


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    tool_count = len(list_tools())
    return {
        "status": "ok",
        "version": "1.0.0",
        "tools_loaded": tool_count,
        "default_model": DEFAULT_MODEL,
        "billing_configured": billing_client.configured,
    }


@app.post("/chat")
async def chat(req: ChatRequest):
    """High-level chat endpoint with tool detection."""
    message = req.message.strip()
    model = req.model or DEFAULT_MODEL
    session_id = req.session_id or "default"

    response_text, tool_used = await _handle_message(message, session_id, model)
    result = {"response": response_text, "model": model}
    if tool_used:
        result["tool_used"] = tool_used
    return result


@app.post("/tool")
async def tool_dispatch(req: ToolRequest):
    """Direct tool execution by name."""
    result = await execute_tool(req.tool, req.args)
    return result


@app.get("/tools")
def tools_list():
    """List all available tools."""
    return {"tools": list_tools()}


# ---------------------------------------------------------------------------
# Ollama-compatible shim routes (handled directly, no Ollama dependency)
# ---------------------------------------------------------------------------

@app.get("/api/tags")
def api_tags():
    """Ollama-compatible model list — served directly by assistant-core."""
    return {
        "models": [
            {
                "name": MODEL_NAME,
                "model": MODEL_NAME,
                "modified_at": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
                "size": 0,
                "digest": "",
                "details": {
                    "parent_model": "",
                    "format": "api",
                    "family": "assistant",
                    "parameter_size": "0",
                    "quantization_level": "none",
                },
            }
        ]
    }


@app.post("/api/chat")
async def api_chat(req: OllamaChatRequest, request: Request):
    """Ollama-compatible chat shim — handled directly by assistant-core.

    Accepts Ollama chat format, processes through tool detection,
    returns Ollama-compatible JSON. Non-streaming.
    """
    model = req.model or MODEL_NAME
    messages = req.messages or []
    session_id = _resolve_session_id(request, messages, req.chat_id)

    # Extract the last user message for processing
    user_message = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            user_message = msg.get("content", "")
            break

    if not user_message:
        return _ollama_chat_response("No message provided.", model)

    response_text, _tool = await _handle_message(user_message, session_id, model)
    return _ollama_chat_response(response_text, model)


@app.post("/api/generate")
async def api_generate(req: OllamaGenerateRequest, request: Request):
    """Ollama-compatible generate shim — handled directly by assistant-core.

    Accepts Ollama generate format, processes through tool detection,
    returns Ollama-compatible JSON. Non-streaming.
    """
    model = req.model or MODEL_NAME
    prompt = req.prompt.strip()
    session_id = _resolve_session_id(request, chat_id=req.chat_id)

    if not prompt:
        return _ollama_generate_response("No prompt provided.", model)

    response_text, _tool = await _handle_message(prompt, session_id, model)
    return _ollama_generate_response(response_text, model)


# ---------------------------------------------------------------------------
# WhatsApp inbound webhook
# ---------------------------------------------------------------------------

@app.post("/webhooks/whatsapp/inbound")
async def whatsapp_inbound(
    request: Request,
    MessageSid: str = Form(""),
    From: str = Form(""),
    To: str = Form(""),
    Body: str = Form(""),
    WaId: str = Form(""),
    ProfileName: str = Form(""),
):
    """Twilio WhatsApp inbound webhook.

    Receives inbound messages, looks up sender by phone in billing,
    replies with a greeting. Deduplicates by MessageSid.
    """
    dedup: WhatsAppDedup = request.app.state.wa_dedup

    # Reject if no MessageSid
    if not MessageSid:
        logger.warning("WhatsApp inbound: missing MessageSid")
        return Response(content=twiml_empty(), media_type="application/xml")

    # Dedup check
    if dedup.is_duplicate(MessageSid):
        logger.info(f"WhatsApp inbound: duplicate {MessageSid}, skipping")
        return Response(content=twiml_empty(), media_type="application/xml")

    # Normalize phone
    phone = normalize_phone(From)
    logger.info(f"WhatsApp inbound: sid={MessageSid} from={phone} profile={ProfileName}")

    # Mark as processed immediately (before API call) to prevent race conditions
    dedup.mark_processed(MessageSid, phone)

    # Look up client by phone
    reply_text = ""
    match_count = "n/a"
    try:
        if not billing_client.configured:
            reply_text = "Hi \U0001f44b"
        else:
            data = billing_client.client_by_phone(phone)
            clients = data.get("clients", []) if data.get("success") else []
            match_count = len(clients)

            if len(clients) == 1:
                name = clients[0].get("fullname", "")
                reply_text = f"Hi {name} \U0001f44b"
            elif len(clients) == 0:
                reply_text = "Hi \U0001f44b We could not match your number to a client yet."
            else:
                reply_text = "Hi \U0001f44b We found more than one client for this number. Please contact support."

        logger.info(f"WhatsApp inbound: sid={MessageSid} matched={match_count}")
    except Exception as e:
        logger.error(f"WhatsApp inbound: sid={MessageSid} billing lookup error: {e}")
        reply_text = "Hi \U0001f44b"

    return Response(content=twiml_reply(reply_text), media_type="application/xml")
