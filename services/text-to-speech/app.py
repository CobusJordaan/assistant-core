"""Text-to-Speech microservice — Piper (local) + Edge TTS (cloud)."""
#
import asyncio
import io
import logging
import os
import struct
import subprocess
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("tts")

app = FastAPI(title="Text-to-Speech (Piper + Edge TTS)")

PIPER_DIR = os.getenv("PIPER_DIR", "/opt/piper")
PIPER_BIN = os.path.join(PIPER_DIR, "piper")
MODELS_DIR = os.getenv("PIPER_MODELS_DIR", os.path.join(PIPER_DIR, "models"))
DEFAULT_VOICE = os.getenv("PIPER_DEFAULT_VOICE", "en_US-lessac-medium")
SAMPLE_RATE = 22050  # Piper default

# Edge TTS built-in voices (Afrikaans)
EDGE_VOICES = [
    {"id": "af-ZA-AdriNeural", "name": "Afrikaans - Adri (Female)", "provider": "edge"},
    {"id": "af-ZA-WillemNeural", "name": "Afrikaans - Willem (Male)", "provider": "edge"},
]

# Check if edge-tts is available
try:
    import edge_tts
    EDGE_TTS_AVAILABLE = True
except ImportError:
    EDGE_TTS_AVAILABLE = False
    logger.warning("edge-tts not installed — Edge TTS voices unavailable")


class SpeechRequest(BaseModel):
    input: str
    voice: str = ""


def _is_edge_voice(voice: str) -> bool:
    """Check if a voice name is an Edge TTS voice (ends with Neural)."""
    return voice.endswith("Neural")


def _pcm_to_wav(pcm_data: bytes, sample_rate: int = SAMPLE_RATE, channels: int = 1, sample_width: int = 2) -> bytes:
    """Wrap raw PCM data in a WAV header."""
    data_size = len(pcm_data)
    buf = io.BytesIO()
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", 36 + data_size))
    buf.write(b"WAVE")
    buf.write(b"fmt ")
    buf.write(struct.pack("<I", 16))
    buf.write(struct.pack("<H", 1))
    buf.write(struct.pack("<H", channels))
    buf.write(struct.pack("<I", sample_rate))
    buf.write(struct.pack("<I", sample_rate * channels * sample_width))
    buf.write(struct.pack("<H", channels * sample_width))
    buf.write(struct.pack("<H", sample_width * 8))
    buf.write(b"data")
    buf.write(struct.pack("<I", data_size))
    buf.write(pcm_data)
    return buf.getvalue()


def _find_model(voice_name: str) -> str | None:
    """Find the .onnx model file for a given voice name."""
    models_path = Path(MODELS_DIR)
    if not models_path.is_dir():
        return None
    exact = models_path / f"{voice_name}.onnx"
    if exact.exists():
        return str(exact)
    for onnx in models_path.rglob(f"*{voice_name}*.onnx"):
        return str(onnx)
    return None


async def _synthesize_edge(text: str, voice: str) -> bytes:
    """Synthesize speech using Edge TTS. Returns MP3 bytes."""
    communicate = edge_tts.Communicate(text, voice)
    buf = io.BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buf.write(chunk["data"])
    return buf.getvalue()


async def _synthesize_piper(text: str, voice: str) -> bytes:
    """Synthesize speech using Piper. Returns WAV bytes."""
    model_path = _find_model(voice)
    if not model_path:
        raise HTTPException(400, f"Voice model not found: {voice}")
    if not os.path.isfile(PIPER_BIN):
        raise HTTPException(503, f"Piper binary not found at {PIPER_BIN}")

    loop = asyncio.get_event_loop()
    proc = await loop.run_in_executor(
        None,
        lambda: subprocess.run(
            [PIPER_BIN, "--model", model_path, "--output_raw"],
            input=text.encode("utf-8"),
            capture_output=True,
            timeout=60,
        ),
    )

    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace")[:500]
        logger.error("Piper failed (rc=%d): %s", proc.returncode, stderr)
        raise HTTPException(500, f"Piper error: {stderr}")

    return _pcm_to_wav(proc.stdout)


@app.get("/health")
async def health():
    piper_exists = os.path.isfile(PIPER_BIN)
    models = []
    models_path = Path(MODELS_DIR)
    if models_path.is_dir():
        models = [f.stem for f in models_path.glob("*.onnx")]
    return {
        "status": "ok" if piper_exists else "piper_not_found",
        "piper_binary": PIPER_BIN,
        "piper_exists": piper_exists,
        "models_dir": MODELS_DIR,
        "models_available": len(models),
        "edge_tts_available": EDGE_TTS_AVAILABLE,
    }


@app.get("/v1/voices")
async def list_voices():
    models_path = Path(MODELS_DIR)
    voices = []
    # Piper voices
    if models_path.is_dir():
        for f in sorted(models_path.glob("*.onnx")):
            voices.append({"id": f.stem, "name": f.stem, "provider": "piper"})
    # Edge TTS voices
    if EDGE_TTS_AVAILABLE:
        voices.extend(EDGE_VOICES)
    return {"voices": voices}


@app.post("/v1/audio/speech")
async def synthesize(req: SpeechRequest):
    text = req.input.strip()
    if not text:
        raise HTTPException(400, "No input text provided")

    voice = req.voice or DEFAULT_VOICE

    logger.info("TTS request: voice=%s, provider=%s, text_len=%d",
                voice, "edge" if _is_edge_voice(voice) else "piper", len(text))

    try:
        if _is_edge_voice(voice):
            if not EDGE_TTS_AVAILABLE:
                raise HTTPException(503, "Edge TTS is not installed")
            mp3_data = await _synthesize_edge(text, voice)
            logger.info("Edge TTS complete: %d bytes MP3", len(mp3_data))
            return Response(content=mp3_data, media_type="audio/mpeg")
        else:
            wav_data = await _synthesize_piper(text, voice)
            logger.info("Piper TTS complete: %d bytes WAV", len(wav_data))
            return Response(content=wav_data, media_type="audio/wav")

    except HTTPException:
        raise
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "TTS generation timed out")
    except Exception as e:
        logger.error("TTS error: %s", e)
        raise HTTPException(500, str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5400)
