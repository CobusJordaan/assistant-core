# assistant-core

AI assistant backend with Ollama-compatible API, structured memory, extensible tool routing, and billing integration.

## Features

- Ollama-compatible endpoints (`/api/chat`, `/api/generate`, `/api/tags`)
- Structured key/value memory with namespace support
- Tool routing with built-in network tools (ping, dns, http, tcp)
- Billing read-only integration (client lookup, balance, invoices, summary)
- Open WebUI compatible

## Quick Start (Dev)

```bash
cd assistant-core
python -m venv venv
source venv/bin/activate   # Linux/Mac
# or: venv\Scripts\activate  # Windows
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your values
uvicorn app:app --host 0.0.0.0 --port 8000
```

## Deploy (AI PC)

### First-Time Setup

```bash
cd /opt
git clone https://github.com/CobusJordaan/assistant-core.git
cd assistant-core
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with production values

sudo cp assistant-core.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable assistant-core
sudo systemctl start assistant-core
```

### Update

```bash
cd /opt/assistant-core
git pull
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart assistant-core
```

### Check Status

```bash
sudo systemctl status assistant-core
journalctl -u assistant-core -f
curl http://localhost:8000/health
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| POST | `/chat` | Chat with auto tool detection |
| POST | `/tool` | Direct tool dispatch |
| GET | `/tools` | List available tools |
| GET | `/api/tags` | Ollama-compatible model list |
| POST | `/api/chat` | Ollama-compatible chat |
| POST | `/api/generate` | Ollama-compatible generate |
| POST | `/memory/set` | Set a memory key/value |
| GET | `/memory/get` | Get memory by namespace+key |
| GET | `/memory/list` | List entries in a namespace |
| DELETE | `/memory/delete` | Delete a memory entry |

## Tool Commands

### Network Tools
- `ping <host>` — Ping a host
- `dns_lookup <hostname>` — DNS resolution
- `http_check <url>` — HTTP reachability check
- `tcp_check <host> <port>` — TCP port check

### Billing Tools (requires API config)
- `find client <query>` — Search clients
- `client balance <id>` — Account balance
- `unpaid invoices <id>` — Unpaid invoice list
- `client summary <id>` — Full client overview

## Configuration

All config via `.env` — see `.env.example` for all options.

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_URL` | `http://localhost:11434` | Ollama endpoint |
| `DEFAULT_MODEL` | `llama3.2` | Default model name |
| `BILLING_ASSISTANT_BASE_URL` | — | Billing API base URL |
| `BILLING_ASSISTANT_API_TOKEN` | — | Billing API bearer token |
| `BILLING_ASSISTANT_TIMEOUT` | `15` | Billing API timeout (seconds) |
| `DATABASE_PATH` | `memory.db` | SQLite memory database path |
| `SECRET_KEY` | — | Application secret key |
