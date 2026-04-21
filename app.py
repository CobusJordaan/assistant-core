"""assistant-core — AI assistant backend with Ollama-compatible API."""

import os
import json
import time
import logging
from contextlib import asynccontextmanager

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Any

# Load .env before anything reads os.getenv
load_dotenv()

from memory_api import MemoryStore, memory_router
from tools import register_tool, list_tools, execute_tool
from tool_intent import detect_intent
from billing_client import billing_client

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "llama3.2")
SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "You are a helpful AI assistant. Answer concisely and accurately.",
)
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
# Ollama HTTP helper
# ---------------------------------------------------------------------------

async def _ollama_post(path: str, body: dict) -> dict:
    """POST to Ollama and return JSON response."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=300, write=10, pool=10)) as client:
        resp = await client.post(f"{OLLAMA_URL}{path}", json=body)
        resp.raise_for_status()
        return resp.json()


async def _ollama_get(path: str) -> dict:
    """GET from Ollama and return JSON response."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{OLLAMA_URL}{path}")
        resp.raise_for_status()
        return resp.json()


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
        "ollama_url": OLLAMA_URL,
        "default_model": DEFAULT_MODEL,
        "billing_configured": billing_client.configured,
    }


@app.post("/chat")
async def chat(req: ChatRequest):
    """High-level chat endpoint with tool detection.

    Flow:
    1. Check for tool intent in the message
    2. If tool detected, execute it and return result
    3. Otherwise, forward to Ollama and return response
    """
    message = req.message.strip()
    model = req.model or DEFAULT_MODEL

    # Check for tool intent
    intent = detect_intent(message)
    if intent:
        result = await execute_tool(intent["tool"], intent.get("args"))
        return {"response": result, "tool_used": intent["tool"], "model": model}

    # Forward to Ollama
    try:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": message},
        ]
        result = await _ollama_post("/api/chat", {
            "model": model,
            "messages": messages,
            "stream": False,
        })
        assistant_content = result.get("message", {}).get("content", "")
        return {"response": assistant_content, "model": model}
    except httpx.HTTPError as e:
        logger.error(f"/chat Ollama error: {e}")
        return JSONResponse(
            status_code=502,
            content={"error": f"Ollama unavailable: {e}"},
        )


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
# Ollama-compatible shim routes
# ---------------------------------------------------------------------------

@app.get("/api/tags")
async def api_tags():
    """Ollama-compatible model list."""
    try:
        return await _ollama_get("/api/tags")
    except httpx.HTTPError as e:
        logger.error(f"/api/tags Ollama error: {e}")
        return JSONResponse(status_code=502, content={"error": f"Ollama unavailable: {e}"})


@app.post("/api/chat")
async def api_chat(req: OllamaChatRequest):
    """Ollama-compatible chat endpoint.

    Accepts Ollama chat format, forwards to Ollama, returns Ollama response format.
    Stream parameter is accepted but responses are non-streaming.
    """
    model = req.model or DEFAULT_MODEL
    messages = req.messages or []

    # Inject system prompt if not already present
    if not any(m.get("role") == "system" for m in messages):
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + messages

    body = {"model": model, "messages": messages, "stream": False}
    if req.options:
        body["options"] = req.options

    try:
        result = await _ollama_post("/api/chat", body)
        return result
    except httpx.HTTPError as e:
        logger.error(f"/api/chat Ollama error: {e}")
        return JSONResponse(status_code=502, content={"error": f"Ollama unavailable: {e}"})


@app.post("/api/generate")
async def api_generate(req: OllamaGenerateRequest):
    """Ollama-compatible generate endpoint.

    Accepts Ollama generate format, forwards to Ollama, returns Ollama response format.
    Stream parameter is accepted but responses are non-streaming.
    """
    model = req.model or DEFAULT_MODEL
    body = {"model": model, "prompt": req.prompt, "stream": False}
    if req.options:
        body["options"] = req.options

    try:
        result = await _ollama_post("/api/generate", body)
        return result
    except httpx.HTTPError as e:
        logger.error(f"/api/generate Ollama error: {e}")
        return JSONResponse(status_code=502, content={"error": f"Ollama unavailable: {e}"})
