"""AI Router — OpenAI-compatible chat completions proxy with intent-based routing.

Exposes a single "Draadloze AI" model that routes to the right Ollama model
or Image Bridge based on message content.
"""

import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from auth import validate_bearer_token
from config import RouterConfig, load_config
from intent import detect_intent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("ai-router")

_config: RouterConfig | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _config
    logger.info("AI Router starting up...")
    _config = load_config()
    logger.info(
        "AI Router ready — port=%s, ollama=%s, models: general=%s code=%s vision=%s",
        _config.port, _config.ollama_base_url,
        _config.model_general, _config.model_code, _config.model_vision,
    )

    # Test Ollama connectivity
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{_config.ollama_base_url}/api/tags")
            if resp.status_code == 200:
                models = [m["name"] for m in resp.json().get("models", [])]
                logger.info("Ollama connected, available models: %s", models)
            else:
                logger.warning("Ollama responded with status %d", resp.status_code)
    except Exception as e:
        logger.warning("Ollama not reachable at %s: %s", _config.ollama_base_url, e)

    yield
    logger.info("AI Router shutting down")


app = FastAPI(
    title="AI Router",
    description="Draadloze AI — intelligent model router for Open WebUI",
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _require_auth(authorization: str | None, x_admin_test: str | None):
    if _config is None:
        raise HTTPException(status_code=503, detail="Service not initialized")
    validate_bearer_token(_config, authorization, x_admin_test)


# ---------------------------------------------------------------------------
# Request/Response models
# ---------------------------------------------------------------------------

class ChatMessage(BaseModel):
    role: str
    content: str | list = ""

class ChatCompletionRequest(BaseModel):
    model: str = ""
    messages: list[ChatMessage] = Field(default_factory=list)
    stream: bool = True
    temperature: float = 0.7
    max_tokens: int | None = None
    top_p: float | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    ollama_ok = False
    if _config:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{_config.ollama_base_url}/api/tags")
                ollama_ok = resp.status_code == 200
        except Exception:
            pass
    return {"status": "ok", "ollama_connected": ollama_ok, "version": "1.0.0"}


@app.get("/v1/models")
async def list_models(
    authorization: str | None = Header(None),
    x_admin_test: str | None = Header(None, alias="X-Admin-Test"),
):
    """Return single Draadloze AI model for Open WebUI."""
    _require_auth(authorization, x_admin_test)

    name = _config.display_name if _config else "Draadloze AI"
    model_id = _config.display_id if _config else "draadloze-ai"

    return {
        "object": "list",
        "data": [
            {
                "id": model_id,
                "object": "model",
                "created": 0,
                "owned_by": "draadloze-ai",
                "name": name,
            }
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(
    req: ChatCompletionRequest,
    authorization: str | None = Header(None),
    x_admin_test: str | None = Header(None, alias="X-Admin-Test"),
):
    """OpenAI-compatible chat completions with intent-based routing."""
    _require_auth(authorization, x_admin_test)

    if not _config:
        raise HTTPException(status_code=503, detail="Service not ready")

    # Extract last user message
    user_text = ""
    has_images = False
    for msg in reversed(req.messages):
        if msg.role == "user":
            if isinstance(msg.content, list):
                # Multimodal message (text + images)
                for part in msg.content:
                    if isinstance(part, dict):
                        if part.get("type") == "text":
                            user_text = part.get("text", "")
                        elif part.get("type") == "image_url":
                            has_images = True
                    elif isinstance(part, str):
                        user_text = part
            else:
                user_text = str(msg.content)
            break

    if not user_text and not has_images:
        raise HTTPException(status_code=400, detail="No user message found")

    # Detect intent
    intent = detect_intent(user_text, has_images)
    logger.info("Intent: %s | Message: %r", intent, user_text[:100])

    # Route based on intent
    if intent == "image_gen":
        return await _handle_image_generation(user_text, req)

    # Select Ollama model
    model_map = {
        "general": _config.model_general,
        "code": _config.model_code,
        "vision": _config.model_vision,
    }
    ollama_model = model_map.get(intent, _config.model_general)
    logger.info("Routing to Ollama model: %s", ollama_model)

    # Build Ollama messages
    ollama_messages = []
    for msg in req.messages:
        if isinstance(msg.content, list):
            # Convert multimodal format for Ollama
            text_parts = []
            images = []
            for part in msg.content:
                if isinstance(part, dict):
                    if part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
                    elif part.get("type") == "image_url":
                        url = part.get("image_url", {}).get("url", "")
                        # Extract base64 data from data URI
                        if url.startswith("data:"):
                            b64 = url.split(",", 1)[1] if "," in url else ""
                            if b64:
                                images.append(b64)
            entry = {"role": msg.role, "content": " ".join(text_parts)}
            if images:
                entry["images"] = images
            ollama_messages.append(entry)
        else:
            ollama_messages.append({"role": msg.role, "content": str(msg.content)})

    if req.stream:
        return StreamingResponse(
            _stream_ollama(ollama_model, ollama_messages, req),
            media_type="text/event-stream",
        )
    else:
        return await _non_streaming_ollama(ollama_model, ollama_messages, req)


# ---------------------------------------------------------------------------
# Ollama streaming proxy
# ---------------------------------------------------------------------------

async def _stream_ollama(
    model: str, messages: list[dict], req: ChatCompletionRequest
) -> AsyncIterator[str]:
    """Stream Ollama response as OpenAI SSE chunks."""
    chat_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())

    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "options": {},
    }
    if req.temperature is not None:
        payload["options"]["temperature"] = req.temperature
    if req.max_tokens is not None:
        payload["options"]["num_predict"] = req.max_tokens
    if req.top_p is not None:
        payload["options"]["top_p"] = req.top_p

    ollama_url = f"{_config.ollama_base_url}/api/chat"
    timeouts = httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0)

    try:
        async with httpx.AsyncClient(timeout=timeouts) as client:
            async with client.stream("POST", ollama_url, json=payload) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    logger.error("Ollama error %d: %s", resp.status_code, body[:500])
                    error_chunk = {
                        "id": chat_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": _config.display_id,
                        "choices": [{"delta": {"content": f"Error: Ollama returned {resp.status_code}"}, "index": 0, "finish_reason": None}],
                    }
                    yield f"data: {json.dumps(error_chunk)}\n\n"
                    yield "data: [DONE]\n\n"
                    return

                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    content = data.get("message", {}).get("content", "")
                    done = data.get("done", False)

                    if content:
                        chunk = {
                            "id": chat_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": _config.display_id,
                            "choices": [{
                                "delta": {"content": content},
                                "index": 0,
                                "finish_reason": None,
                            }],
                        }
                        yield f"data: {json.dumps(chunk)}\n\n"

                    if done:
                        final_chunk = {
                            "id": chat_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": _config.display_id,
                            "choices": [{
                                "delta": {},
                                "index": 0,
                                "finish_reason": "stop",
                            }],
                        }
                        yield f"data: {json.dumps(final_chunk)}\n\n"

    except httpx.ConnectError:
        error_chunk = {
            "id": chat_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": _config.display_id,
            "choices": [{"delta": {"content": "Error: Cannot connect to Ollama. Is it running?"}, "index": 0, "finish_reason": None}],
        }
        yield f"data: {json.dumps(error_chunk)}\n\n"
    except Exception as e:
        logger.error("Streaming error: %s", e)
        error_chunk = {
            "id": chat_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": _config.display_id,
            "choices": [{"delta": {"content": f"Error: {e}"}, "index": 0, "finish_reason": None}],
        }
        yield f"data: {json.dumps(error_chunk)}\n\n"

    yield "data: [DONE]\n\n"


async def _non_streaming_ollama(
    model: str, messages: list[dict], req: ChatCompletionRequest
) -> dict:
    """Non-streaming Ollama call, returns OpenAI completion format."""
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {},
    }
    if req.temperature is not None:
        payload["options"]["temperature"] = req.temperature
    if req.max_tokens is not None:
        payload["options"]["num_predict"] = req.max_tokens
    if req.top_p is not None:
        payload["options"]["top_p"] = req.top_p

    ollama_url = f"{_config.ollama_base_url}/api/chat"
    timeouts = httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0)

    async with httpx.AsyncClient(timeout=timeouts) as client:
        resp = await client.post(ollama_url, json=payload)
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Ollama error: {resp.status_code}")
        data = resp.json()

    content = data.get("message", {}).get("content", "")
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": _config.display_id,
        "choices": [{
            "message": {"role": "assistant", "content": content},
            "index": 0,
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": data.get("prompt_eval_count", 0),
            "completion_tokens": data.get("eval_count", 0),
            "total_tokens": data.get("prompt_eval_count", 0) + data.get("eval_count", 0),
        },
    }


# ---------------------------------------------------------------------------
# Image generation handler
# ---------------------------------------------------------------------------

async def _handle_image_generation(user_text: str, req: ChatCompletionRequest) -> dict:
    """Route image generation requests to Image Bridge."""
    logger.info("Routing to Image Bridge for image generation")

    if not _config.image_bridge_url:
        return _chat_response("Image generation is not configured. Ask the admin to set up Image Bridge.")

    headers = {}
    if _config.image_bridge_api_key:
        headers["Authorization"] = f"Bearer {_config.image_bridge_api_key}"
    else:
        headers["X-Admin-Test"] = "true"

    body = {
        "prompt": user_text,
        "n": 1,
        "size": "512x640",
        "response_format": "url",
    }

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{_config.image_bridge_url}/v1/images/generations",
                json=body,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.error("Image Bridge error: %s", e)
        return _chat_response(f"Sorry, image generation failed: {e}")

    # Build markdown response with image URLs
    images = data.get("data", [])
    if not images:
        return _chat_response("Image generation returned no results.")

    parts = []
    for img in images:
        url = img.get("url", "")
        prompt = img.get("revised_prompt", user_text)
        if url:
            parts.append(f"![{prompt}]({url})")
        else:
            parts.append("(Image generated but no URL available)")

    return _chat_response("\n\n".join(parts))


def _chat_response(content: str) -> dict:
    """Build a non-streaming OpenAI chat completion response."""
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": _config.display_id if _config else "draadloze-ai",
        "choices": [{
            "message": {"role": "assistant", "content": content},
            "index": 0,
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"message": exc.detail, "type": "error", "code": exc.status_code}},
    )


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("AI_ROUTER_PORT", "5100"))
    uvicorn.run(app, host="0.0.0.0", port=port)
