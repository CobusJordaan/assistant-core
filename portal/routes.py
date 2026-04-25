"""Portal routes — login, chat, conversations, voice."""

import json
import logging
import os
import re
import secrets
import uuid
from datetime import date
from pathlib import Path

import bcrypt as _bcrypt
import httpx
from fastapi import APIRouter, Request, Form, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse, FileResponse
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


def _get_voice_config(db) -> dict:
    """Read voice-related admin settings."""
    config = {}
    keys = ["voice_enabled", "allow_browser_stt", "allow_whisper_fallback",
            "tts_piper_url", "tts_voice", "stt_whisper_url", "voice_max_seconds",
            "voice_audio_dir", "stt_provider"]
    if db and db.available:
        for key in keys:
            row = db.get_setting_raw(key)
            config[key] = row["value"] if row else ""
    return config


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

    voice_cfg = _get_voice_config(db)

    response = templates.TemplateResponse(request, "portal/chat.html", {
        "session": session,
        "conversations": conversations,
        "active_conv": None,
        "messages": [],
        "vision_allowed": user.get("vision_allowed", 1) if user else 1,
        "voice_allowed": user.get("voice_allowed", 1) if user else 0,
        "voice_enabled": voice_cfg.get("voice_enabled") == "true",
        "allow_browser_stt": voice_cfg.get("allow_browser_stt") == "true",
        "allow_whisper_fallback": voice_cfg.get("allow_whisper_fallback") == "true",
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
    voice_cfg = _get_voice_config(db)

    response = templates.TemplateResponse(request, "portal/chat.html", {
        "session": session,
        "conversations": conversations,
        "active_conv": conv,
        "messages": messages,
        "vision_allowed": user.get("vision_allowed", 1) if user else 1,
        "voice_allowed": user.get("voice_allowed", 1) if user else 0,
        "voice_enabled": voice_cfg.get("voice_enabled") == "true",
        "allow_browser_stt": voice_cfg.get("allow_browser_stt") == "true",
        "allow_whisper_fallback": voice_cfg.get("allow_whisper_fallback") == "true",
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


# ---------------------------------------------------------------------------
# Voice API
# ---------------------------------------------------------------------------

@router.post("/api/voice/transcribe")
async def api_voice_transcribe(request: Request):
    """Forward audio to Whisper STT for transcription."""
    session = _require_session(request)
    if not session:
        return JSONResponse(status_code=401, content={"error": "Not logged in"})

    db = _get_db(request)
    user = db.get_portal_user_by_id(session["user_id"]) if db else None
    if not user or not user.get("voice_allowed", 0):
        return JSONResponse(status_code=403, content={"error": "Voice is not enabled for your account."})

    voice_cfg = _get_voice_config(db)
    if voice_cfg.get("voice_enabled") != "true":
        return JSONResponse(status_code=503, content={"error": "Voice is not available yet."})

    # Parse multipart form with audio file
    form = await request.form()
    audio_file = form.get("file")
    if not audio_file:
        return JSONResponse(status_code=400, content={"error": "No audio file provided"})

    audio_bytes = await audio_file.read()
    audio_size = len(audio_bytes)
    logger.info("Voice transcribe: user=%s, audio_size=%d, content_type=%s",
                session["user"], audio_size, getattr(audio_file, "content_type", "unknown"))

    whisper_url = voice_cfg.get("stt_whisper_url", "http://127.0.0.1:5300")

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            files = {"file": ("audio.webm", audio_bytes, getattr(audio_file, "content_type", "audio/webm"))}
            data = {"model": "whisper-1"}
            resp = await client.post(f"{whisper_url}/v1/audio/transcriptions", files=files, data=data)

            if resp.status_code != 200:
                logger.error("Whisper error: status=%d body=%s", resp.status_code, resp.text[:500])
                return JSONResponse(status_code=502, content={"error": "Transcription failed."})

            result = resp.json()
            text = result.get("text", "").strip()
            logger.info("Whisper transcription: len=%d", len(text))
            return {"text": text}

    except httpx.ConnectError:
        return JSONResponse(status_code=502, content={"error": "Cannot connect to Whisper service."})
    except Exception as e:
        logger.error("Transcribe error: %s", e)
        return JSONResponse(status_code=500, content={"error": "Transcription failed."})


@router.post("/api/voice")
async def api_voice(request: Request):
    """Process voice message: get AI reply + TTS audio."""
    session = _require_session(request)
    if not session:
        return JSONResponse(status_code=401, content={"error": "Not logged in"})

    db = _get_db(request)
    if not db:
        return JSONResponse(status_code=503, content={"error": "Database unavailable"})

    user = db.get_portal_user_by_id(session["user_id"])
    if not user or not user["is_active"]:
        return JSONResponse(status_code=403, content={"error": "Account disabled"})
    if not user.get("voice_allowed", 0):
        return JSONResponse(status_code=403, content={"error": "Voice is not enabled for your account."})

    voice_cfg = _get_voice_config(db)
    if voice_cfg.get("voice_enabled") != "true":
        return JSONResponse(status_code=503, content={"error": "Voice is not available yet."})

    body = await request.json()
    transcript = body.get("transcript", "").strip()
    conv_id = body.get("conversation_id")

    if not transcript:
        return JSONResponse(status_code=400, content={"error": "Empty transcript"})

    user_id = session["user_id"]
    today = date.today().isoformat()

    # Check daily limits
    usage = db.get_daily_usage(user_id, today)
    if user["daily_message_limit"] > 0 and usage["message_count"] >= user["daily_message_limit"]:
        return JSONResponse(status_code=429, content={"error": "Daily message limit reached"})

    # Create conversation if needed
    if not conv_id:
        title = transcript[:50] + ("..." if len(transcript) > 50 else "")
        conv_id = db.create_conversation(user_id, title)
    else:
        conv = db.get_conversation(conv_id)
        if not conv or conv["user_id"] != user_id:
            return JSONResponse(status_code=403, content={"error": "Access denied"})

    # Save user message
    db.add_message(conv_id, "user", transcript)
    db.increment_usage(user_id, today, messages=1)

    # Build messages for AI Router
    history = db.get_messages(conv_id)
    ai_messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for msg in history:
        ai_messages.append({"role": msg["role"], "content": msg["content"]})

    logger.info("Voice request: user=%s, transcript_len=%d, conv=%d", session["user"], len(transcript), conv_id)

    # Call AI Router (non-streaming for voice)
    reply_text = ""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0)) as client:
            payload = {"model": "draadloze-ai", "messages": ai_messages, "stream": False}
            resp = await client.post(
                f"{AI_ROUTER_URL}/v1/chat/completions",
                json=payload,
                headers={"X-Admin-Test": "true"},
            )
            if resp.status_code != 200:
                logger.error("AI Router error: status=%d", resp.status_code)
                return JSONResponse(status_code=502, content={"error": "AI service error"})

            result = resp.json()
            reply_text = result.get("choices", [{}])[0].get("message", {}).get("content", "")
    except httpx.ConnectError:
        return JSONResponse(status_code=502, content={"error": "Cannot connect to AI service."})
    except Exception as e:
        logger.error("AI Router error: %s", e)
        return JSONResponse(status_code=500, content={"error": "AI service error"})

    if not reply_text:
        return JSONResponse(status_code=500, content={"error": "Empty AI response"})

    # Save assistant message
    db.add_message(conv_id, "assistant", reply_text)

    # Generate TTS audio — clean markdown and limit length for speech
    audio_url = ""
    tts_url = voice_cfg.get("tts_piper_url", "http://127.0.0.1:5400")
    tts_voice = voice_cfg.get("tts_voice", "en_US-lessac-medium")
    audio_dir = voice_cfg.get("voice_audio_dir", "/opt/ai-assistant/data/portal/audio")

    # Strip markdown formatting for cleaner speech
    tts_text = reply_text
    tts_text = re.sub(r'#{1,6}\s+', '', tts_text)           # headings
    tts_text = re.sub(r'\*{1,3}(.+?)\*{1,3}', r'\1', tts_text)  # bold/italic
    tts_text = re.sub(r'`{1,3}[^`]*`{1,3}', '', tts_text)  # inline/block code
    tts_text = re.sub(r'^\s*[-*]\s+', '', tts_text, flags=re.MULTILINE)  # bullet points
    tts_text = re.sub(r'^\s*\d+\.\s+', '', tts_text, flags=re.MULTILINE)  # numbered lists
    tts_text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', tts_text)  # links
    tts_text = re.sub(r'\n{2,}', '. ', tts_text)            # paragraph breaks
    tts_text = re.sub(r'\n', ' ', tts_text)                  # remaining newlines
    tts_text = re.sub(r'\s{2,}', ' ', tts_text).strip()     # extra whitespace

    # Truncate to ~500 chars at sentence boundary for long responses
    MAX_TTS_CHARS = 500
    if len(tts_text) > MAX_TTS_CHARS:
        truncated = tts_text[:MAX_TTS_CHARS]
        # Cut at last sentence boundary
        for sep in ['. ', '! ', '? ']:
            idx = truncated.rfind(sep)
            if idx > MAX_TTS_CHARS // 2:
                truncated = truncated[:idx + 1]
                break
        tts_text = truncated

    try:
        os.makedirs(audio_dir, exist_ok=True)
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
            tts_resp = await client.post(
                f"{tts_url}/v1/audio/speech",
                json={"input": tts_text, "voice": tts_voice},
            )
            if tts_resp.status_code == 200:
                filename = f"{uuid.uuid4().hex}.wav"
                filepath = os.path.join(audio_dir, filename)
                with open(filepath, "wb") as f:
                    f.write(tts_resp.content)
                audio_url = f"/portal/api/audio/{filename}"
                logger.info("TTS audio saved: %s (%d bytes)", filename, len(tts_resp.content))
            else:
                logger.error("TTS error: status=%d", tts_resp.status_code)
    except Exception as e:
        logger.error("TTS error: %s", e)

    return {
        "transcript": transcript,
        "reply_text": reply_text,
        "audio_url": audio_url,
        "conversation_id": conv_id,
    }


@router.get("/api/audio/{filename}")
async def api_audio(request: Request, filename: str):
    """Serve voice audio files."""
    session = _require_session(request)
    if not session:
        return JSONResponse(status_code=401, content={"error": "Not logged in"})

    # Validate filename to prevent path traversal
    if not re.match(r"^[a-f0-9]+\.wav$", filename):
        return JSONResponse(status_code=400, content={"error": "Invalid filename"})

    db = _get_db(request)
    voice_cfg = _get_voice_config(db)
    audio_dir = voice_cfg.get("voice_audio_dir", "/opt/ai-assistant/data/portal/audio")
    filepath = os.path.join(audio_dir, filename)

    if not os.path.isfile(filepath):
        return JSONResponse(status_code=404, content={"error": "Audio not found"})

    return FileResponse(filepath, media_type="audio/wav")
