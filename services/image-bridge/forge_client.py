"""Forge API client for Stable Diffusion image generation."""

import base64
import logging
import os
import time
from pathlib import Path

import httpx

logger = logging.getLogger("image-bridge.forge")


class ForgeClient:
    """Client for Forge/Automatic1111 Stable Diffusion WebUI API."""

    def __init__(self, base_url: str, txt2img_endpoint: str, img2img_endpoint: str,
                 output_dir: str, timeout: float = 120):
        self.base_url = base_url.rstrip("/")
        self.txt2img_url = self.base_url + txt2img_endpoint
        self.img2img_url = self.base_url + img2img_endpoint
        self.output_dir = output_dir
        self.timeout = timeout

    async def test_connection(self) -> bool:
        """Test if Forge is reachable."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(self.base_url)
                return resp.status_code < 500
        except Exception:
            return False

    async def get_models(self) -> list[dict]:
        """Get available SD models from Forge."""
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{self.base_url}/sdapi/v1/sd-models")
                if resp.status_code == 200:
                    models = resp.json()
                    return [
                        {
                            "id": m.get("model_name", m.get("title", "unknown")),
                            "object": "model",
                            "created": 0,
                            "owned_by": "local-forge",
                        }
                        for m in models
                    ]
        except Exception as e:
            logger.error("Failed to get models from Forge: %s", e)
        return []

    async def get_raw_models(self) -> list[dict]:
        """Get raw SD model list from Forge (for checkpoint dropdown)."""
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{self.base_url}/sdapi/v1/sd-models")
                if resp.status_code == 200:
                    return resp.json()
        except Exception as e:
            logger.error("Failed to get models from Forge: %s", e)
        return []

    async def txt2img(
        self,
        prompt: str,
        negative_prompt: str = "",
        width: int = 512,
        height: int = 640,
        steps: int = 35,
        cfg_scale: float = 7,
        sampler_name: str = "DPM++ 2M SDE",
        scheduler: str = "Karras",
        model: str = "",
        n: int = 1,
        checkpoint: str = "",
        enable_adetailer: bool = False,
        adetailer_model: str = "face_yolov8n.pt",
        adetailer_prompt: str = "",
        adetailer_negative_prompt: str = "",
    ) -> list[dict]:
        """Generate images via Forge txt2img API.

        Returns list of dicts with b64_json and/or saved file path.
        """
        payload = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "width": width,
            "height": height,
            "steps": steps,
            "cfg_scale": cfg_scale,
            "sampler_name": sampler_name,
            "scheduler": scheduler,
            "batch_size": min(n, 4),
            "n_iter": 1,
        }

        # Set checkpoint via override_settings
        effective_checkpoint = checkpoint or model
        if effective_checkpoint:
            payload["override_settings"] = {"sd_model_checkpoint": effective_checkpoint}
            payload["override_settings_restore_afterwards"] = True

        # ADetailer extension
        if enable_adetailer:
            ad_args = {"ad_model": adetailer_model}
            if adetailer_prompt:
                ad_args["ad_prompt"] = adetailer_prompt
            if adetailer_negative_prompt:
                ad_args["ad_negative_prompt"] = adetailer_negative_prompt
            payload["alwayson_scripts"] = {"ADetailer": {"args": [ad_args]}}

        # Log payload summary (no base64)
        logger.info(
            "Forge txt2img: prompt=%r, size=%dx%d, steps=%d, cfg=%.1f, "
            "sampler=%s, scheduler=%s, checkpoint=%s, adetailer=%s",
            prompt[:80], width, height, steps, cfg_scale,
            sampler_name, scheduler, effective_checkpoint, enable_adetailer,
        )
        logger.info("Forge txt2img payload keys: %s", list(payload.keys()))

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(self.txt2img_url, json=payload)
            resp.raise_for_status()

        data = resp.json()
        images = data.get("images", [])
        results = []

        for i, b64 in enumerate(images[:n]):
            saved_path = self._save_image(b64, prompt, i)
            results.append({
                "b64_json": b64,
                "revised_prompt": prompt,
            })

        logger.info("Forge txt2img returned %d image(s)", len(results))
        return results

    def _save_image(self, b64_data: str, prompt: str, index: int) -> str | None:
        """Save base64 image to output directory."""
        if not self.output_dir:
            return None

        try:
            out_dir = Path(self.output_dir)
            out_dir.mkdir(parents=True, exist_ok=True)

            timestamp = int(time.time())
            # Sanitize prompt for filename
            safe_prompt = "".join(c if c.isalnum() or c in " -_" else "" for c in prompt[:40]).strip()
            safe_prompt = safe_prompt.replace(" ", "_") or "image"
            filename = f"{timestamp}_{safe_prompt}_{index}.png"

            filepath = out_dir / filename
            img_bytes = base64.b64decode(b64_data)
            filepath.write_bytes(img_bytes)

            logger.info("Saved image: %s", filepath)
            return str(filepath)
        except Exception as e:
            logger.warning("Failed to save image: %s", e)
            return None
