"""Tool registry and built-in tools."""

import socket
import subprocess
import httpx
from typing import Any, Callable, Awaitable


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_tools: dict[str, dict] = {}


def register_tool(name: str, fn: Callable[..., Any], description: str = "", parameters: dict | None = None):
    """Register a tool function."""
    _tools[name] = {
        "name": name,
        "description": description,
        "parameters": parameters or {},
        "fn": fn,
    }


def get_tool(name: str) -> dict | None:
    return _tools.get(name)


def list_tools() -> list[dict]:
    """Return metadata for all registered tools."""
    return [
        {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}
        for t in _tools.values()
    ]


async def execute_tool(name: str, args: dict | None = None) -> dict:
    """Execute a tool by name. Returns result dict."""
    tool = _tools.get(name)
    if not tool:
        return {"error": f"Unknown tool: {name}", "available": [t["name"] for t in _tools.values()]}
    try:
        result = tool["fn"](**(args or {}))
        # Support both sync and async tool functions
        if hasattr(result, "__await__"):
            result = await result
        return {"tool": name, "result": result}
    except Exception as e:
        return {"tool": name, "error": str(e)}


# ---------------------------------------------------------------------------
# Built-in tools
# ---------------------------------------------------------------------------

def tool_ping(host: str, count: int = 4) -> dict:
    """Ping a host and return results."""
    try:
        # Use -n on Windows, -c on Linux
        import platform
        flag = "-n" if platform.system().lower() == "windows" else "-c"
        result = subprocess.run(
            ["ping", flag, str(count), host],
            capture_output=True, text=True, timeout=30,
        )
        return {"host": host, "output": result.stdout, "success": result.returncode == 0}
    except subprocess.TimeoutExpired:
        return {"host": host, "error": "Ping timed out", "success": False}
    except Exception as e:
        return {"host": host, "error": str(e), "success": False}


def tool_dns_lookup(hostname: str) -> dict:
    """Resolve a hostname to IP addresses."""
    try:
        results = socket.getaddrinfo(hostname, None)
        ips = sorted(set(r[4][0] for r in results))
        return {"hostname": hostname, "addresses": ips, "success": True}
    except socket.gaierror as e:
        return {"hostname": hostname, "error": str(e), "success": False}


def tool_http_check(url: str, timeout: int = 10) -> dict:
    """Check if a URL is reachable and return status."""
    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=True)
        return {
            "url": url,
            "status_code": resp.status_code,
            "content_length": len(resp.content),
            "success": resp.status_code < 400,
        }
    except Exception as e:
        return {"url": url, "error": str(e), "success": False}


def tool_tcp_check(host: str, port: int, timeout: int = 5) -> dict:
    """Check if a TCP port is open."""
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        return {"host": host, "port": port, "open": True, "success": True}
    except (socket.timeout, ConnectionRefusedError, OSError) as e:
        return {"host": host, "port": port, "open": False, "error": str(e), "success": False}


# ---------------------------------------------------------------------------
# Register built-in tools
# ---------------------------------------------------------------------------

register_tool("ping", tool_ping, "Ping a host", {"host": {"type": "string", "required": True}, "count": {"type": "integer", "default": 4}})
register_tool("dns_lookup", tool_dns_lookup, "DNS lookup for a hostname", {"hostname": {"type": "string", "required": True}})
register_tool("http_check", tool_http_check, "Check if a URL is reachable", {"url": {"type": "string", "required": True}, "timeout": {"type": "integer", "default": 10}})
register_tool("tcp_check", tool_tcp_check, "Check if a TCP port is open", {"host": {"type": "string", "required": True}, "port": {"type": "integer", "required": True}})
