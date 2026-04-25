"""Settings loader — reads image-bridge config from admin.db app_settings."""

import os
import sqlite3
from dataclasses import dataclass, field


@dataclass
class ImageBridgeConfig:
    """Image bridge configuration loaded from admin.db."""

    enabled: bool = False
    host: str = "0.0.0.0"
    port: int = 5000
    forge_base_url: str = "http://127.0.0.1:7860"
    forge_txt2img_endpoint: str = "/sdapi/v1/txt2img"
    forge_img2img_endpoint: str = "/sdapi/v1/img2img"
    default_width: int = 512
    default_height: int = 512
    default_steps: int = 20
    default_cfg_scale: int = 7
    default_sampler: str = "Euler a"
    default_model: str = ""
    output_dir: str = "/opt/ai-assistant/data/image-bridge/output"

    # Auth (loaded separately)
    api_key_hash: str = ""
    api_key_salt: str = ""


_SETTING_MAP = {
    "image_bridge_enabled": ("enabled", lambda v: v.lower() in ("true", "1", "yes")),
    "image_bridge_host": ("host", str),
    "image_bridge_port": ("port", int),
    "forge_base_url": ("forge_base_url", str),
    "forge_txt2img_endpoint": ("forge_txt2img_endpoint", str),
    "forge_img2img_endpoint": ("forge_img2img_endpoint", str),
    "default_width": ("default_width", int),
    "default_height": ("default_height", int),
    "default_steps": ("default_steps", int),
    "default_cfg_scale": ("default_cfg_scale", int),
    "default_sampler": ("default_sampler", str),
    "default_model": ("default_model", str),
    "output_dir": ("output_dir", str),
    "image_bridge_api_key_hash": ("api_key_hash", str),
    "image_bridge_api_key_salt": ("api_key_salt", str),
}


def load_config(db_path: str | None = None) -> ImageBridgeConfig:
    """Load config from admin.db app_settings table (read-only)."""
    if db_path is None:
        db_path = os.getenv("ADMIN_DB_PATH", "/opt/ai-assistant/data/admin.db")

    config = ImageBridgeConfig()

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
