"""Portal routes — login, chat, conversations."""

import json
import logging
import os
import secrets
from datetime import date

import bcrypt as _bcrypt
import httpx
from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from portal.auth import (
    check_rate_limit, record_failed_attempt, clear_attempts,
    get_lockout_remaining, verify_portal_user,
    create_portal_session, get_portal_session, refresh_portal_session,
    clear_portal_session,
)

logger = logging.getLogger("portal")

router = APIRouter(prefix="/portal", tags=["portal"])
templates = Jinja2Templates(directory="templates")

AI_ROUTER_URL = "http://127.0.0.1:5100"
SYSTEM_PROMPT = "You are Draadloze AI, a helpful assistant for a family environment."
DEFAULT_VISION_PROMPT = "Please summarize this image clearly. If it contains text, extract and explain the important parts."
MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10 MB
MAX_IMAGES_PER_MSG = 4
ALLOWED_IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp"}


def _get_db(request: Request):
    return getattr(request.app.state, "admin_db", None)


def _require_session(request: Request) -> dict | None:
    return get_portal_session(request)


# ---------------------------------------------------------------------------
# Auth pages
# ---------------------------------------------------------------------------

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    session = get_portal_session(request)
    if session:
        return RedirectResponse("/portal", status_code=302)
    return templates.TemplateResponse(request, "portal/login.html", {"error": None})


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(ip):
        remaining = get_lockout_remaining(ip)
        return templates.TemplateResponse(request, "portal/login.html", {
            "error": f"Too many attempts. Try again in {remaining // 60 + 1} minutes.",
        })

    db = _get_db(request)
    user = verify_portal_user(username, password, db)
    if not user:
        record_failed_attempt(ip)
        return templates.TemplateResponse(request, "portal/login.html", {
            "error": "Invalid username or password.",
        })

    clear_attempts(ip)
    # Update last_login_at
    db.update_portal_user(user["id"], last_login_at=__import__("datetime").datetime.now(
        __import__("datetime").timezone.utc).isoformat())

    response = RedirectResponse("/portal", status_code=302)
    create_portal_session(response, user)
    return response


@router.get("/logout")
async def logout(request: Request):
    response = RedirectResponse("/portal/login", status_code=302)
    clear_portal_session(response)
    return response


# ---------------------------------------------------------------------------
# Chat page
# ---------------------------------------------------------------------------

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def chat_page(request: Request):
    session = _require_session(request)
    if not session:
        return RedirectResponse("/portal/login", status_code=302)

    db = _get_db(request)
    conversations = db.list_conversations(session["user_id"]) if db else []
    user = db.get_portal_user_by_id(session["user_id"]) if db else None

    response = templates.TemplateResponse(request, "portal/chat.html", {
        "session": session,
        "conversations": conversations,
        "active_conv": None,
        "messages": [],
        "vision_allowed": user.get("vision_allowed", 1) if user else 1,
    })
    refresh_portal_session(request, response)
    return response


@router.get("/c/{conv_id}", response_class=HTMLResponse)
async def chat_conversation(request: Request, conv_id: int):
    session = _require_session(request)
    if not session:
        return RedirectResponse("/portal/login", status_code=302)

    db = _get_db(request)
    conv = db.get_conversation(conv_id) if db else None
    if not conv or conv["user_id"] != session["user_id"]:
        return RedirectResponse("/portal", status_code=302)

    conversations = db.list_conversations(session["user_id"])
    messages = db.get_messages(conv_id)
    user = db.get_portal_user_by_id(session["user_id"]) if db else None

    response = templates.TemplateResponse(request, "portal/chat.html", {
        "session": session,
        "conversations": conversations,
        "active_conv": conv,
        "messages": messages,
        "vision_allowed": user.get("vision_allowed", 1) if user else 1,
    })
    refresh_portal_session(request, response)
    return response


# ---------------------------------------------------------------------------
# Chat API
# ---------------------------------------------------------------------------

@router.post("/api/chat")
async def api_chat(request: Request):
    """Send message and stream AI response."""
    session = _require_session(request)
    if not session:
        return JSONResponse(status_code=401, content={"error": "Not logged in"})

    db = _get_db(request)
    if not db:
        return JSONResponse(status_code=503, content={"error": "Database unavailable"})

    body = await request.json()
    message = body.get("message", "").strip()
    conv_id = body.get("conversation_id")
    images = body.get("images", [])  # list of data URI strings

    if not message and not images:
        return JSONResponse(status_code=400, content={"error": "Empty message"})

    user_id = session["user_id"]
    today = date.today().isoformat()

    # Check user still active
    user = db.get_portal_user_by_id(user_id)
    if not user or not user["is_active"]:
        return JSONResponse(status_code=403, content={"error": "Account disabled"})

    # Validate images if present
    if images:
        if not user.get("vision_allowed", 1):
            return JSONResponse(status_code=403, content={"error": "Image uploads are not enabled for your account."})

        if len(images) > MAX_IMAGES_PER_MSG:
            return JSONResponse(status_code=400, content={"error": f"Maximum {MAX_IMAGES_PER_MSG} images per message."})

        mime_types = []
        for i, data_uri in enumerate(images):
            # Validate data URI format: data:image/jpeg;base64,...
            if not data_uri.startswith("data:"):
                return JSONResponse(status_code=400, content={"error": "Invalid image format."})
            header = data_uri.split(",", 1)[0]  # e.g. "data:image/jpeg;base64"
            mime = header.split(":")[1].split(";")[0] if ":" in header else ""
            if mime not in ALLOWED_IMAGE_MIMES:
                return JSONResponse(status_code=400, content={
                    "error": "Invalid file type. Only JPG, PNG, and WebP images are allowed."
                })
            # Check size (base64 is ~4/3 of original)
            b64_data = data_uri.split(",", 1)[1] if "," in data_uri else ""
            approx_size = len(b64_data) * 3 // 4
            if approx_size > MAX_IMAGE_SIZE:
                return JSONResponse(status_code=400, content={
                    "error": "Image too large. Maximum size is 10MB."
                })
            mime_types.append(mime)

        logger.info("Vision upload: user=%s, images=%d, mimes=%s",
                     session["user"], len(images), mime_types)

    # Default prompt if images but no text
    if images and not message:
        message = DEFAULT_VISION_PROMPT

    # Check daily limits
    usage = db.get_daily_usage(user_id, today)
    if user["daily_message_limit"] > 0 and usage["message_count"] >= user["daily_message_limit"]:
        return JSONResponse(status_code=429, content={"error": "Daily message limit reached"})

    # Create conversation if needed
    if not conv_id:
        title = message[:50] + ("..." if len(message) > 50 else "")
        conv_id = db.create_conversation(user_id, title)

    # Verify conversation ownership
    conv = db.get_conversation(conv_id)
    if not conv or conv["user_id"] != user_id:
        return JSONResponse(status_code=403, content={"error": "Access denied"})

    # Save user message (text only, no base64)
    db.add_message(conv_id, "user", message)
    db.increment_usage(user_id, today, messages=1)

    # Build messages for AI Router (include history)
    history = db.get_messages(conv_id)
    ai_messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for msg in history:
        ai_messages.append({"role": msg["role"], "content": msg["content"]})

    # If images present, replace the last user message with multimodal format
    if images:
        content_parts = [{"type": "text", "text": message}]
        for data_uri in images:
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": data_uri},
            })
        # Replace last message (which is the current user message text)
        ai_messages[-1] = {"role": "user", "content": content_parts}
        logger.info("Built multimodal message with %d image(s), intent=vision", len(images))

    # Stream response from AI Router
    return StreamingResponse(
        _stream_chat(db, conv_id, user_id, today, ai_messages),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Conversation-Id": str(conv_id)},
    )


async def _stream_chat(db, conv_id: int, user_id: int, today: str, messages: list):
    """Stream AI Router response as SSE to the browser."""
    full_response = ""
    image_url = ""

    try:
        timeouts = httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0)
        async with httpx.AsyncClient(timeout=timeouts) as client:
            payload = {
                "model": "draadloze-ai",
                "messages": messages,
                "stream": True,
            }
            logger.info("Sending to AI Router: stream=True, messages=%d", len(messages))
            async with client.stream("POST", f"{AI_ROUTER_URL}/v1/chat/completions",
                                      json=payload,
                                      headers={"X-Admin-Test": "true"}) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    logger.error("AI Router error: status=%d body=%s", resp.status_code, body[:500])
                    error_msg = f"AI service error: {resp.status_code}"
                    yield f"data: {json.dumps({'error': error_msg})}\n\n"
                    yield "data: [DONE]\n\n"
                    return

                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break

                    try:
                        chunk = json.loads(data_str)
                        content = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                        if content:
                            full_response += content
                            yield f"data: {json.dumps({'content': content})}\n\n"
                    except json.JSONDecodeError:
                        continue

    except httpx.ConnectError:
        error_msg = "Cannot connect to AI service. Please try again later."
        yield f"data: {json.dumps({'error': error_msg})}\n\n"
        full_response = error_msg
    except Exception as e:
        logger.error("Stream error: %s", e)
        error_msg = "Something went wrong. Please try again."
        yield f"data: {json.dumps({'error': error_msg})}\n\n"
        full_response = error_msg

    # Extract image URL from markdown if present (e.g. ![alt](http://...))
    import re
    img_match = re.search(r'!\[[^\]]*\]\((https?://[^)]+)\)', full_response)
    if img_match:
        image_url = img_match.group(1)
        logger.info("Image URL detected in response: %s", image_url)

    # Save assistant response
    if full_response:
        db.add_message(conv_id, "assistant", full_response, image_url=image_url)
        logger.info("Saved assistant message: len=%d, has_image=%s", len(full_response), bool(image_url))

    yield "data: [DONE]\n\n"


# ---------------------------------------------------------------------------
# Conversation API
# ---------------------------------------------------------------------------

@router.get("/api/conversations")
async def api_list_conversations(request: Request):
    session = _require_session(request)
    if not session:
        return JSONResponse(status_code=401, content={"error": "Not logged in"})
    db = _get_db(request)
    convs = db.list_conversations(session["user_id"])
    return {"conversations": convs}


@router.post("/api/conversations")
async def api_create_conversation(request: Request):
    session = _require_session(request)
    if not session:
        return JSONResponse(status_code=401, content={"error": "Not logged in"})
    db = _get_db(request)
    conv_id = db.create_conversation(session["user_id"])
    return {"id": conv_id}


@router.delete("/api/conversations/{conv_id}")
async def api_delete_conversation(request: Request, conv_id: int):
    session = _require_session(request)
    if not session:
        return JSONResponse(status_code=401, content={"error": "Not logged in"})
    db = _get_db(request)
    conv = db.get_conversation(conv_id)
    if not conv or conv["user_id"] != session["user_id"]:
        return JSONResponse(status_code=403, content={"error": "Access denied"})
    db.delete_conversation(conv_id)
    return {"success": True}


@router.get("/api/conversations/{conv_id}/messages")
async def api_get_messages(request: Request, conv_id: int):
    session = _require_session(request)
    if not session:
        return JSONResponse(status_code=401, content={"error": "Not logged in"})
    db = _get_db(request)
    conv = db.get_conversation(conv_id)
    if not conv or conv["user_id"] != session["user_id"]:
        return JSONResponse(status_code=403, content={"error": "Access denied"})
    messages = db.get_messages(conv_id)
    return {"messages": messages}


@router.get("/api/usage")
async def api_usage(request: Request):
    session = _require_session(request)
    if not session:
        return JSONResponse(status_code=401, content={"error": "Not logged in"})
    db = _get_db(request)
    usage = db.get_daily_usage(session["user_id"], date.today().isoformat())
    user = db.get_portal_user_by_id(session["user_id"])
    return {
        "message_count": usage["message_count"],
        "image_count": usage["image_count"],
        "daily_message_limit": user["daily_message_limit"] if user else 0,
        "daily_image_limit": user["daily_image_limit"] if user else 0,
    }
