# Image Bridge

OpenAI-compatible image generation API that bridges Open WebUI to a local Forge (Stable Diffusion WebUI) instance.

## Overview

Image Bridge exposes an OpenAI-compatible `/v1/images/generations` endpoint that translates requests into Forge's `/sdapi/v1/txt2img` API. This allows Open WebUI to use local Stable Diffusion image generation as if it were DALL-E.

## Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/health` | No | Health check + Forge connection status |
| GET | `/v1/models` | Bearer | List available SD models from Forge |
| POST | `/v1/images/generations` | Bearer | Generate images (OpenAI-compatible) |

## Setup

### 1. Install

```bash
cd /opt/ai-assistant/services/image-bridge
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure

Create `/etc/image-bridge.env`:

```env
ADMIN_DB_PATH=/opt/ai-assistant/data/admin.db
IMAGE_BRIDGE_PORT=5000
```

Settings are managed through the assistant-core admin dashboard at `/admin/image-bridge`.

### 3. Generate API Key

In the admin dashboard, go to Image Bridge → Generate Key. Save the key — it's shown only once.

### 4. Install systemd service

```bash
sudo cp image-bridge.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable image-bridge
sudo systemctl start image-bridge
```

### 5. Connect Open WebUI

In Open WebUI Admin Settings → Images:

- **Image Generation Engine**: OpenAI DALL·E
- **URL**: `http://172.18.2.195:5000`
- **API Key**: The key generated in step 3

## Architecture

```
Open WebUI  →  Image Bridge (port 5000)  →  Forge (port 7860)
   POST /v1/images/generations              POST /sdapi/v1/txt2img
   Bearer token auth                        No auth (localhost)
```

## Configuration (via admin.db app_settings)

| Setting | Default | Description |
|---------|---------|-------------|
| `forge_base_url` | `http://127.0.0.1:7860` | Forge API URL |
| `default_width` | `512` | Default image width |
| `default_height` | `512` | Default image height |
| `default_steps` | `20` | Diffusion steps |
| `default_cfg_scale` | `7` | Classifier-free guidance scale |
| `default_sampler` | `Euler a` | Sampling method |
| `default_model` | (empty) | SD model checkpoint (empty = Forge default) |
| `output_dir` | `/opt/ai-assistant/data/image-bridge/output` | Save generated images |

## Security

- Bearer token required for all generation endpoints
- API key hash stored in admin.db (never the raw key)
- Only accessible on LAN/VPN — do not expose to public internet
- Health endpoint is unauthenticated (monitoring use)
