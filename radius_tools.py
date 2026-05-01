"""RADIUS network tools — check client online status and ping their IP."""

import subprocess
import platform
from billing_client import billing_client


def tool_client_radius_status(client_id: int) -> dict:
    """Check if a client is online via RADIUS and return their IP, MAC, and NAS."""
    if not billing_client.configured:
        return {"error": "Billing API not configured", "success": False}
    try:
        data = billing_client.client_radius_status(client_id)
        if not data.get("success"):
            return {"error": data.get("error", "Unknown error"), "success": False}
        return data
    except Exception as e:
        return {"error": str(e), "success": False}


def tool_client_ping(client_id: int, count: int = 4) -> dict:
    """Get a client's IP from RADIUS then ping it. Returns RADIUS status + ping result."""
    if not billing_client.configured:
        return {"error": "Billing API not configured", "success": False}

    # Step 1: Get RADIUS status
    try:
        data = billing_client.client_radius_status(client_id)
    except Exception as e:
        return {"error": f"Failed to fetch RADIUS status: {e}", "success": False}

    if not data.get("success"):
        return {"error": data.get("error", "Unknown error"), "success": False}

    if not data.get("is_online"):
        return {
            "success": True,
            "client_id": client_id,
            "client_name": data.get("client_name"),
            "client_number": data.get("client_number"),
            "is_online": False,
            "ping": None,
            "message": "Client is offline — cannot ping.",
        }

    ip = data.get("ip_address")
    if not ip:
        return {
            "success": True,
            "client_id": client_id,
            "client_name": data.get("client_name"),
            "is_online": True,
            "ping": None,
            "message": "Client is online but has no IP address assigned.",
        }

    # Step 2: Ping the IP
    flag = "-n" if platform.system().lower() == "windows" else "-c"
    try:
        result = subprocess.run(
            ["ping", flag, str(count), ip],
            capture_output=True, text=True, timeout=30,
        )
        ping_success = result.returncode == 0
        ping_output = result.stdout.strip()
    except subprocess.TimeoutExpired:
        ping_success = False
        ping_output = "Ping timed out"
    except Exception as e:
        ping_success = False
        ping_output = str(e)

    return {
        "success": True,
        "client_id": client_id,
        "client_name": data.get("client_name"),
        "client_number": data.get("client_number"),
        "is_online": True,
        "ip_address": ip,
        "mac_address": data.get("mac_address"),
        "nas_ip": data.get("nas_ip"),
        "session_start": data.get("session_start"),
        "ping": {
            "host": ip,
            "success": ping_success,
            "output": ping_output,
        },
    }
