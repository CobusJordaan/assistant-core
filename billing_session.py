"""Lightweight in-memory selected-client context per session."""

_sessions: dict[str, dict] = {}


def set_client(session_id: str, client_id: int, client_name: str = ""):
    """Store the selected client for a session."""
    _sessions[session_id] = {"client_id": client_id, "client_name": client_name}


def get_client(session_id: str) -> dict | None:
    """Get the selected client for a session. Returns {"client_id": int, "client_name": str} or None."""
    return _sessions.get(session_id)


def clear_client(session_id: str):
    """Clear the selected client for a session."""
    _sessions.pop(session_id, None)
