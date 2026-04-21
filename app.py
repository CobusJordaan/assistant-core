"""assistant-core — AI assistant backend with Ollama-compatible API."""

import os
import json
import time
import logging
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from pydantic import BaseModel
from typing import Any

# Load .env before anything reads os.getenv
load_dotenv()

from memory_api import MemoryStore, memory_router
from tools import register_tool, list_tools, execute_tool
from tool_intent import detect_intent
from billing_client import billing_client
from billing_format import format_billing_result

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

    _register_billing_tools()
    logger.info(f"assistant-core started — {len(list_tools())} tools loaded")

    yield

    # Shutdown
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
    model: str | None = None
    messages: list[dict] = []
    stream: bool = False
    options: dict | None = None


class OllamaGenerateRequest(BaseModel):
    model: str | None = None
    prompt: str = ""
    stream: bool = False
    options: dict | None = None


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


async def _handle_message(message: str, model: str | None = None) -> tuple[str, str | None]:
    """Process a message through tool detection.

    Returns (response_text, tool_name_or_none).
    """
    intent = detect_intent(message)
    if intent:
        result = await execute_tool(intent["tool"], intent.get("args"))
        tool_name = intent["tool"]

        # Format billing results as clean text
        if tool_name.startswith("billing_"):
            formatted = format_billing_result(tool_name, result)
            if formatted:
                return formatted, tool_name

        return json.dumps(result, indent=2, default=str), tool_name

    return f"Received: {message}", None


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

    response_text, tool_used = await _handle_message(message, model)
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
async def api_chat(req: OllamaChatRequest):
    """Ollama-compatible chat shim — handled directly by assistant-core.

    Accepts Ollama chat format, processes through tool detection,
    returns Ollama-compatible JSON. Non-streaming.
    """
    model = req.model or MODEL_NAME
    messages = req.messages or []

    # Extract the last user message for processing
    user_message = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            user_message = msg.get("content", "")
            break

    if not user_message:
        return _ollama_chat_response("No message provided.", model)

    response_text, _tool = await _handle_message(user_message, model)
    return _ollama_chat_response(response_text, model)


@app.post("/api/generate")
async def api_generate(req: OllamaGenerateRequest):
    """Ollama-compatible generate shim — handled directly by assistant-core.

    Accepts Ollama generate format, processes through tool detection,
    returns Ollama-compatible JSON. Non-streaming.
    """
    model = req.model or MODEL_NAME
    prompt = req.prompt.strip()

    if not prompt:
        return _ollama_generate_response("No prompt provided.", model)

    response_text, _tool = await _handle_message(prompt, model)
    return _ollama_generate_response(response_text, model)
