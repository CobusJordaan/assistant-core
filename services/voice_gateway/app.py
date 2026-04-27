"""Voice Gateway — orchestrates STT, Ollama, and TTS for real-time voice conversations.

Exports `voice_gateway_router` for inclusion in the main assistant-core app,
plus `voice_gateway_startup()` / `voice_gateway_shutdown()` lifecycle hooks.
"""

import asyncio
import base64
import json
import logging
import os
import time
import uuid
from pathlib import Path

import httpx
from fastapi import APIRouter, File, Header, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel

from services.voice_gateway.config import load_config, GatewayConfig
from services.voice_gateway.auth import validate_bearer_token, validate_token_string

logger = logging.getLogger("voice-gateway")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

cfg: GatewayConfig = load_config()


def _reload_config():
    global cfg
    cfg = load_config()


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

class VoiceSession:
    """In-memory voice conversation session."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.messages: list[dict] = []
        self.created_at = time.time()
        self.last_active = time.time()
        self.is_active = True
        self._cancel = False

    def add_message(self, role: str, content: str):
        self.messages.append({"role": role, "content": content})
        self.last_active = time.time()
        if len(self.messages) > cfg.max_history:
            self.messages = self.messages[-cfg.max_history:]

    def get_ollama_messages(self) -> list[dict]:
        msgs = []
        if cfg.system_prompt:
            msgs.append({"role": "system", "content": cfg.system_prompt})
        msgs.extend(self.messages)
        return msgs

    def cancel(self):
        self._cancel = True

    def is_cancelled(self) -> bool:
        return self._cancel

    def reset_cancel(self):
        self._cancel = False

    def close(self):
        self.is_active = False


_sessions: dict[str, VoiceSession] = {}
_SESSION_IDLE_TIMEOUT = 30 * 60  # 30 minutes
_cleanup_tasks: list[asyncio.Task] = []


# ---------------------------------------------------------------------------
# Background tasks
# ---------------------------------------------------------------------------

async def _cleanup_sessions():
    while True:
        await asyncio.sleep(60)
        now = time.time()
        expired = [sid for sid, s in _sessions.items()
                   if now - s.last_active > _SESSION_IDLE_TIMEOUT or not s.is_active]
        for sid in expired:
            del _sessions[sid]
        if expired:
            logger.info("Cleaned up %d idle sessions", len(expired))


async def _cleanup_audio_files():
    while True:
        await asyncio.sleep(60)
        temp_dir = Path(cfg.audio_temp_dir)
        if not temp_dir.is_dir():
            continue
        cutoff = time.time() - cfg.audio_cleanup_seconds
        count = 0
        for f in temp_dir.iterdir():
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
                count += 1
        if count:
            logger.info("Cleaned up %d expired audio files", count)


# ---------------------------------------------------------------------------
# Lifecycle hooks (called from main app lifespan)
# ---------------------------------------------------------------------------

def voice_gateway_startup():
    """Call during app startup to init voice gateway."""
    _reload_config()
    os.makedirs(cfg.audio_temp_dir, exist_ok=True)
    _cleanup_tasks.append(asyncio.create_task(_cleanup_sessions()))
    _cleanup_tasks.append(asyncio.create_task(_cleanup_audio_files()))
    logger.info("Voice Gateway started")
    logger.info("  STT: %s", cfg.stt_url)
    logger.info("  TTS: %s", cfg.tts_url)
    logger.info("  Ollama: %s (model: %s)", cfg.ollama_url, cfg.ollama_model)


def voice_gateway_shutdown():
    """Call during app shutdown to clean up."""
    for task in _cleanup_tasks:
        task.cancel()
    _cleanup_tasks.clear()


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

voice_gateway_router = APIRouter(tags=["voice-gateway"])


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class TTSRequest(BaseModel):
    input: str
    voice: str = ""


# ---------------------------------------------------------------------------
# HTTP Endpoints
# ---------------------------------------------------------------------------

@voice_gateway_router.get("/voice-gateway/health")
async def health():
    """Check STT, TTS, and Ollama connectivity."""
    stt_ok = False
    tts_ok = False
    ollama_ok = False
    ollama_models = []

    async with httpx.AsyncClient(timeout=5) as client:
        try:
            resp = await client.get(f"{cfg.stt_url.rstrip('/')}/health")
            stt_ok = resp.status_code == 200
        except Exception:
            pass

        try:
            resp = await client.get(f"{cfg.tts_url.rstrip('/')}/health")
            tts_ok = resp.status_code == 200
        except Exception:
            pass

        try:
            resp = await client.get(f"{cfg.ollama_url.rstrip('/')}/api/tags")
            if resp.status_code == 200:
                ollama_ok = True
                data = resp.json()
                ollama_models = [m["name"] for m in data.get("models", [])]
        except Exception:
            pass

    all_ok = stt_ok and tts_ok and ollama_ok
    return {
        "status": "ok" if all_ok else "degraded",
        "stt": stt_ok,
        "tts": tts_ok,
        "ollama": ollama_ok,
        "ollama_models": ollama_models,
        "model": cfg.ollama_model,
        "voice": cfg.tts_voice,
    }


@voice_gateway_router.post("/api/stt")
async def stt_proxy(
    file: UploadFile = File(...),
    authorization: str | None = Header(None),
    x_admin_test: str | None = Header(None),
):
    """Proxy STT request to Whisper service."""
    validate_bearer_token(cfg, authorization, x_admin_test)

    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(400, "Empty audio file")

    text = await _call_stt(audio_bytes, file.content_type or "audio/webm")
    return {"text": text}


@voice_gateway_router.post("/api/tts")
async def tts_proxy(
    req: TTSRequest,
    authorization: str | None = Header(None),
    x_admin_test: str | None = Header(None),
):
    """Proxy TTS request to Piper service, save audio and return file_id."""
    validate_bearer_token(cfg, authorization, x_admin_test)

    text = req.input.strip()
    if not text:
        raise HTTPException(400, "Empty text")

    voice = req.voice or cfg.tts_voice
    audio_bytes, content_type = await _call_tts(text, voice)

    ext = ".mp3" if "mpeg" in content_type else ".wav"
    file_id = uuid.uuid4().hex
    file_path = Path(cfg.audio_temp_dir) / f"{file_id}{ext}"
    file_path.write_bytes(audio_bytes)

    return {"file_id": file_id, "format": ext.lstrip(".")}


@voice_gateway_router.get("/audio/{file_id}")
async def serve_audio(
    file_id: str,
    token: str | None = None,
    authorization: str | None = Header(None),
    x_admin_test: str | None = Header(None),
):
    """Serve a temp audio file. Auth via header or ?token= query param."""
    if authorization:
        validate_bearer_token(cfg, authorization, x_admin_test)
    elif token:
        if not validate_token_string(cfg, token):
            raise HTTPException(401, "Invalid token")
    else:
        raise HTTPException(401, "Authentication required")

    clean_id = file_id.replace(".wav", "").replace(".mp3", "")
    if not clean_id or not all(c in "0123456789abcdef" for c in clean_id):
        raise HTTPException(404, "Audio not found")

    temp_dir = Path(cfg.audio_temp_dir)
    for ext in (".wav", ".mp3"):
        path = temp_dir / f"{clean_id}{ext}"
        if path.exists():
            media = "audio/wav" if ext == ".wav" else "audio/mpeg"
            return FileResponse(str(path), media_type=media)

    raise HTTPException(404, "Audio not found or expired")


# ---------------------------------------------------------------------------
# Core pipeline functions
# ---------------------------------------------------------------------------

async def _call_stt(audio_bytes: bytes, content_type: str) -> str:
    ext_map = {
        "audio/webm": ".webm", "audio/wav": ".wav", "audio/mpeg": ".mp3",
        "audio/mp4": ".m4a", "audio/ogg": ".ogg", "audio/x-wav": ".wav",
    }
    ext = ext_map.get(content_type, ".webm")
    logger.info("STT request: %d bytes (%s)", len(audio_bytes), content_type)

    async with httpx.AsyncClient(timeout=30) as client:
        files = {"file": (f"audio{ext}", audio_bytes, content_type)}
        data = {"model": "whisper-1"}
        resp = await client.post(
            f"{cfg.stt_url.rstrip('/')}/v1/audio/transcriptions",
            files=files,
            data=data,
        )
        resp.raise_for_status()
        text = resp.json().get("text", "").strip()
        logger.info("STT result: \"%s\"", text[:100] if text else "(empty)")
        return text


async def _call_tts(text: str, voice: str | None = None) -> tuple[bytes, str]:
    voice = voice or cfg.tts_voice
    if len(text) > 4000:
        text = text[:4000]
    logger.info("TTS request: %d chars, voice=%s", len(text), voice)

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{cfg.tts_url.rstrip('/')}/v1/audio/speech",
            json={"input": text, "voice": voice},
        )
        resp.raise_for_status()
        audio = resp.content
        ct = resp.headers.get("content-type", "audio/wav")
        logger.info("TTS result: %d bytes (%s)", len(audio), ct)
        return audio, ct


async def _stream_ollama(session: VoiceSession, send_fn) -> str:
    messages = session.get_ollama_messages()
    collected = ""
    logger.info("Ollama request: model=%s, %d messages", cfg.ollama_model, len(messages))

    timeouts = httpx.Timeout(connect=5.0, read=float(cfg.ollama_timeout), write=10.0, pool=5.0)
    async with httpx.AsyncClient(timeout=timeouts) as client:
        async with client.stream(
            "POST",
            f"{cfg.ollama_url.rstrip('/')}/api/chat",
            json={
                "model": cfg.ollama_model,
                "messages": messages,
                "stream": True,
                "options": {"temperature": 0.7, "num_predict": 2048},
            },
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if session.is_cancelled():
                    break
                if not line.strip():
                    continue
                data = json.loads(line)
                content = data.get("message", {}).get("content", "")
                if content:
                    collected += content
                    await send_fn({"type": "chunk", "content": content})
                if data.get("done"):
                    break

    logger.info("Ollama result: %d chars", len(collected))
    return collected


# ---------------------------------------------------------------------------
# WebSocket voice session
# ---------------------------------------------------------------------------

@voice_gateway_router.websocket("/voice/session")
async def websocket_voice_session(ws: WebSocket):
    await ws.accept()

    # Step 1: Auth
    try:
        raw = await asyncio.wait_for(ws.receive_text(), timeout=10)
        msg = json.loads(raw)
    except Exception:
        await ws.send_json({"type": "error", "message": "Auth timeout or invalid message"})
        await ws.close(code=4001)
        return

    if msg.get("type") != "auth" or not msg.get("token"):
        await ws.send_json({"type": "error", "message": "First message must be auth"})
        await ws.close(code=4001)
        return

    if not validate_token_string(cfg, msg["token"]):
        await ws.send_json({"type": "error", "message": "Invalid token"})
        await ws.close(code=4001)
        return

    # Step 2: Create session
    session_id = uuid.uuid4().hex[:12]
    session = VoiceSession(session_id)
    _sessions[session_id] = session

    await ws.send_json({"type": "session_start", "session_id": session_id})
    logger.info("Voice session started: %s", session_id)

    # Step 3: Message loop
    try:
        while session.is_active:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            msg_type = msg.get("type", "")

            if msg_type == "end":
                break
            elif msg_type == "interrupt":
                session.cancel()
                continue
            elif msg_type == "audio":
                session.reset_cancel()
                await _handle_voice_turn(ws, session, msg)
            else:
                await ws.send_json({"type": "error", "message": f"Unknown message type: {msg_type}"})

    except WebSocketDisconnect:
        logger.info("Voice session disconnected: %s", session_id)
    except Exception as e:
        logger.error("Voice session error: %s — %s", session_id, e)
        try:
            await ws.send_json({"type": "error", "message": str(e)[:200]})
        except Exception:
            pass
    finally:
        session.close()
        if session_id in _sessions:
            del _sessions[session_id]
        logger.info("Voice session ended: %s", session_id)


async def _handle_voice_turn(ws: WebSocket, session: VoiceSession, msg: dict):
    """Handle one voice turn: audio -> STT -> Ollama -> TTS -> audio back."""
    audio_b64 = msg.get("data", "")
    audio_format = msg.get("format", "webm")
    # Normalize to MIME type — browser sends "webm", we need "audio/webm"
    if not audio_format.startswith("audio/"):
        audio_format = f"audio/{audio_format}"
    if not audio_b64:
        await ws.send_json({"type": "error", "message": "No audio data"})
        return

    try:
        audio_bytes = base64.b64decode(audio_b64)
    except Exception:
        await ws.send_json({"type": "error", "message": "Invalid base64 audio"})
        return

    if len(audio_bytes) < 100:
        await ws.send_json({"type": "error", "message": "Audio too short"})
        return

    # --- STT ---
    try:
        transcript = await _call_stt(audio_bytes, audio_format)
    except Exception as e:
        logger.error("STT error: %s", e)
        await ws.send_json({"type": "error", "message": f"STT error: {str(e)[:100]}"})
        return

    await ws.send_json({"type": "transcript", "text": transcript})

    if not transcript or len(transcript.strip()) < 2:
        return

    # --- Ollama ---
    session.add_message("user", transcript)
    await ws.send_json({"type": "thinking"})

    try:
        async def send_chunk(data):
            await ws.send_json(data)

        full_response = await _stream_ollama(session, send_chunk)
    except Exception as e:
        logger.error("Ollama error: %s", e)
        await ws.send_json({"type": "error", "message": f"LLM error: {str(e)[:100]}"})
        return

    if session.is_cancelled():
        return

    if not full_response.strip():
        await ws.send_json({"type": "error", "message": "Empty response from LLM"})
        return

    session.add_message("assistant", full_response)
    await ws.send_json({"type": "response_done", "text": full_response})

    # --- TTS ---
    try:
        audio_bytes_tts, content_type = await _call_tts(full_response)
        ext = ".mp3" if "mpeg" in content_type else ".wav"
        file_id = uuid.uuid4().hex
        file_path = Path(cfg.audio_temp_dir) / f"{file_id}{ext}"
        file_path.write_bytes(audio_bytes_tts)

        await ws.send_json({"type": "audio", "url": f"/audio/{file_id}{ext}"})
    except Exception as e:
        logger.error("TTS error: %s", e)
        await ws.send_json({"type": "error", "message": f"TTS error: {str(e)[:100]}"})
