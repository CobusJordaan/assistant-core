"""Image Bridge — OpenAI-compatible image generation API backed by Forge.

Exposes /v1/images/generations for Open WebUI to use as an image provider.
"""

import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from config import load_config, ImageBridgeConfig
from auth import validate_bearer_token
from forge_client import ForgeClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("image-bridge")

# Global references set during lifespan
_config: ImageBridgeConfig | None = None
_forge: ForgeClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _config, _forge

    logger.info("Image Bridge starting up...")
    _config = load_config()

    _forge = ForgeClient(
        base_url=_config.forge_base_url,
        txt2img_endpoint=_config.forge_txt2img_endpoint,
        img2img_endpoint=_config.forge_img2img_endpoint,
        output_dir=_config.output_dir,
    )

    connected = await _forge.test_connection()
    if connected:
        logger.info("Forge connected at %s", _config.forge_base_url)
    else:
        logger.warning("Forge not reachable at %s — will retry on requests", _config.forge_base_url)

    logger.info("Image Bridge ready on port %s", _config.port)
    yield
    logger.info("Image Bridge shutting down")


app = FastAPI(
    title="Image Bridge",
    description="OpenAI-compatible image generation API backed by local Forge",
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------

def _require_auth(
    authorization: str | None = Header(None),
    x_admin_test: str | None = Header(None, alias="X-Admin-Test"),
):
    """Validate bearer token or admin test header."""
    if _config is None:
        raise HTTPException(status_code=503, detail="Service not initialized")
    validate_bearer_token(_config, authorization, x_admin_test)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class ImageGenerationRequest(BaseModel):
    prompt: str
    n: int = Field(default=1, ge=1, le=4)
    size: str = Field(default="512x512")
    model: str = Field(default="")
    response_format: str = Field(default="b64_json")
    negative_prompt: str = Field(default="")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    """Health check — also reports Forge connection status."""
    forge_ok = False
    if _forge:
        forge_ok = await _forge.test_connection()

    return {
        "status": "ok",
        "forge_connected": forge_ok,
        "version": "1.0.0",
    }


@app.get("/v1/models")
async def list_models(
    authorization: str | None = Header(None),
    x_admin_test: str | None = Header(None, alias="X-Admin-Test"),
):
    """OpenAI-compatible model list — returns available Forge SD models."""
    _require_auth(authorization, x_admin_test)

    if not _forge:
        return {"object": "list", "data": []}

    models = await _forge.get_models()

    # Always include a default entry
    if not models:
        models = [{"id": "stable-diffusion", "object": "model", "created": 0, "owned_by": "local-forge"}]

    return {"object": "list", "data": models}


@app.post("/v1/images/generations")
async def generate_image(
    req: ImageGenerationRequest,
    authorization: str | None = Header(None),
    x_admin_test: str | None = Header(None, alias="X-Admin-Test"),
):
    """OpenAI-compatible image generation endpoint."""
    _require_auth(authorization, x_admin_test)

    if not _forge or not _config:
        raise HTTPException(status_code=503, detail="Service not ready")

    # Parse size
    try:
        parts = req.size.split("x")
        width = int(parts[0])
        height = int(parts[1]) if len(parts) > 1 else width
    except (ValueError, IndexError):
        width = _config.default_width
        height = _config.default_height

    model = req.model or _config.default_model

    try:
        results = await _forge.txt2img(
            prompt=req.prompt,
            negative_prompt=req.negative_prompt,
            width=width,
            height=height,
            steps=_config.default_steps,
            cfg_scale=_config.default_cfg_scale,
            sampler=_config.default_sampler,
            model=model,
            n=req.n,
        )
    except Exception as e:
        logger.error("Image generation failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Generation failed: {str(e)}")

    return {
        "created": int(time.time()),
        "data": results,
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

    port = int(os.getenv("IMAGE_BRIDGE_PORT", "5000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
