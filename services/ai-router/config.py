"""Settings loader — reads ai-router config from admin.db app_settings."""

import os
import sqlite3
from dataclasses import dataclass


_bool_convert = lambda v: v.lower() in ("true", "1", "yes")


@dataclass
class RouterConfig:
    """AI Router configuration loaded from admin.db."""

    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 5100
    ollama_base_url: str = "http://localhost:11434"
    image_bridge_url: str = "http://127.0.0.1:5000"
    image_bridge_api_key: str = ""
    model_general: str = "qwen2.5:14b"
    model_code: str = "qwen2.5-coder:14b"
    model_vision: str = "qwen2.5vl:7b"
    display_name: str = "Draadloze AI"
    display_id: str = "draadloze-ai"

    # Auth
    api_key_hash: str = ""
    api_key_salt: str = ""


_SETTING_MAP = {
    "ai_router_enabled": ("enabled", _bool_convert),
    "ai_router_host": ("host", str),
    "ai_router_port": ("port", int),
    "ai_router_ollama_url": ("ollama_base_url", str),
    "ai_router_image_bridge_url": ("image_bridge_url", str),
    "ai_router_image_bridge_api_key": ("image_bridge_api_key", str),
    "ai_router_model_general": ("model_general", str),
    "ai_router_model_code": ("model_code", str),
    "ai_router_model_vision": ("model_vision", str),
    "ai_router_display_name": ("display_name", str),
    "ai_router_display_id": ("display_id", str),
    "ai_router_api_key_hash": ("api_key_hash", str),
    "ai_router_api_key_salt": ("api_key_salt", str),
}


def load_config(db_path: str | None = None) -> RouterConfig:
    """Load config from admin.db app_settings table (read-only)."""
    if db_path is None:
        db_path = os.getenv("ADMIN_DB_PATH", "/opt/ai-assistant/data/admin.db")

    config = RouterConfig()

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
