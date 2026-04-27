"""Settings loader — reads voice-gateway config from admin.db app_settings."""

import os
import sqlite3
from dataclasses import dataclass


_bool_convert = lambda v: v.lower() in ("true", "1", "yes")


@dataclass
class GatewayConfig:
    """Voice Gateway configuration loaded from admin.db."""

    host: str = "0.0.0.0"
    port: int = 8100
    stt_url: str = "http://127.0.0.1:5300"
    tts_url: str = "http://127.0.0.1:5400"
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5:14b"
    ollama_timeout: int = 120
    tts_voice: str = "en_US-lessac-medium"
    system_prompt: str = "You are a helpful AI voice assistant. Keep responses concise and conversational."
    max_history: int = 20
    audio_temp_dir: str = "/opt/ai-assistant/data/voice-temp"
    audio_cleanup_seconds: int = 300

    # Auth
    api_key_hash: str = ""
    api_key_salt: str = ""


_SETTING_MAP = {
    "voice_gateway_host": ("host", str),
    "voice_gateway_port": ("port", int),
    "voice_gateway_stt_url": ("stt_url", str),
    "voice_gateway_tts_url": ("tts_url", str),
    "voice_gateway_ollama_url": ("ollama_url", str),
    "voice_gateway_ollama_model": ("ollama_model", str),
    "voice_gateway_ollama_timeout": ("ollama_timeout", int),
    "voice_gateway_tts_voice": ("tts_voice", str),
    "voice_gateway_system_prompt": ("system_prompt", str),
    "voice_gateway_max_history": ("max_history", int),
    "voice_gateway_audio_temp_dir": ("audio_temp_dir", str),
    "voice_gateway_audio_cleanup_seconds": ("audio_cleanup_seconds", int),
    "voice_gateway_api_key_hash": ("api_key_hash", str),
    "voice_gateway_api_key_salt": ("api_key_salt", str),
}


def load_config(db_path: str | None = None) -> GatewayConfig:
    """Load config from admin.db app_settings table (read-only)."""
    if db_path is None:
        db_path = os.getenv("ADMIN_DB_PATH", "/opt/ai-assistant/data/admin.db")

    config = GatewayConfig()

    # Override from env vars
    if os.getenv("STT_URL"):
        config.stt_url = os.getenv("STT_URL")
    if os.getenv("TTS_URL"):
        config.tts_url = os.getenv("TTS_URL")
    if os.getenv("OLLAMA_URL"):
        config.ollama_url = os.getenv("OLLAMA_URL")
    if os.getenv("OLLAMA_MODEL"):
        config.ollama_model = os.getenv("OLLAMA_MODEL")
    if os.getenv("AUDIO_TEMP_DIR"):
        config.audio_temp_dir = os.getenv("AUDIO_TEMP_DIR")

    if not os.path.isfile(db_path):
        return config

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
        conn.close()

        for row in rows:
            key = row["key"]
            if key in _SETTING_MAP:
                attr, converter = _SETTING_MAP[key]
                try:
                    setattr(config, attr, converter(row["value"]))
                except (ValueError, TypeError):
                    pass
    except Exception:
        pass

    return config
