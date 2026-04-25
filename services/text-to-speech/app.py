"""Piper TTS microservice — converts text to speech via local Piper binary."""

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

app = FastAPI(title="Text-to-Speech (Piper)")

PIPER_DIR = os.getenv("PIPER_DIR", "/opt/piper")
PIPER_BIN = os.path.join(PIPER_DIR, "piper")
MODELS_DIR = os.getenv("PIPER_MODELS_DIR", os.path.join(PIPER_DIR, "models"))
DEFAULT_VOICE = os.getenv("PIPER_DEFAULT_VOICE", "en_US-lessac-medium")
SAMPLE_RATE = 22050  # Piper default


class SpeechRequest(BaseModel):
    input: str
    voice: str = ""


def _pcm_to_wav(pcm_data: bytes, sample_rate: int = SAMPLE_RATE, channels: int = 1, sample_width: int = 2) -> bytes:
    """Wrap raw PCM data in a WAV header."""
    data_size = len(pcm_data)
    buf = io.BytesIO()
    # RIFF header
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", 36 + data_size))
    buf.write(b"WAVE")
    # fmt chunk
    buf.write(b"fmt ")
    buf.write(struct.pack("<I", 16))  # chunk size
    buf.write(struct.pack("<H", 1))   # PCM format
    buf.write(struct.pack("<H", channels))
    buf.write(struct.pack("<I", sample_rate))
    buf.write(struct.pack("<I", sample_rate * channels * sample_width))  # byte rate
    buf.write(struct.pack("<H", channels * sample_width))  # block align
    buf.write(struct.pack("<H", sample_width * 8))  # bits per sample
    # data chunk
    buf.write(b"data")
    buf.write(struct.pack("<I", data_size))
    buf.write(pcm_data)
    return buf.getvalue()


def _find_model(voice_name: str) -> str | None:
    """Find the .onnx model file for a given voice name."""
    models_path = Path(MODELS_DIR)
    if not models_path.is_dir():
        return None

    # Try exact match first
    exact = models_path / f"{voice_name}.onnx"
    if exact.exists():
        return str(exact)

    # Try with subdirectory structure
    for onnx in models_path.rglob(f"*{voice_name}*.onnx"):
        return str(onnx)

    return None


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
    }


@app.get("/v1/voices")
async def list_voices():
    models_path = Path(MODELS_DIR)
    voices = []
    if models_path.is_dir():
        for f in sorted(models_path.glob("*.onnx")):
            voices.append({"id": f.stem, "name": f.stem, "path": str(f)})
    return {"voices": voices}


@app.post("/v1/audio/speech")
async def synthesize(req: SpeechRequest):
    text = req.input.strip()
    if not text:
        raise HTTPException(400, "No input text provided")

    voice = req.voice or DEFAULT_VOICE
    model_path = _find_model(voice)
    if not model_path:
        raise HTTPException(400, f"Voice model not found: {voice}")

    if not os.path.isfile(PIPER_BIN):
        raise HTTPException(503, f"Piper binary not found at {PIPER_BIN}")

    logger.info("TTS request: voice=%s, text_len=%d", voice, len(text))

    try:
        proc = subprocess.run(
            [PIPER_BIN, "--model", model_path, "--output_raw"],
            input=text.encode("utf-8"),
            capture_output=True,
            timeout=60,
        )

        if proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", errors="replace")[:500]
            logger.error("Piper failed (rc=%d): %s", proc.returncode, stderr)
            raise HTTPException(500, f"Piper error: {stderr}")

        wav_data = _pcm_to_wav(proc.stdout)
        logger.info("TTS complete: %d bytes WAV", len(wav_data))

        return Response(content=wav_data, media_type="audio/wav")

    except subprocess.TimeoutExpired:
        raise HTTPException(504, "TTS generation timed out")
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        logger.error("TTS error: %s", e)
        raise HTTPException(500, str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5400)
