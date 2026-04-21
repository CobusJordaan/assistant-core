"""Lightweight in-memory selected-client context per session."""

_sessions: dict[str, dict] = {}


def _get_or_create(session_id: str) -> dict:
    if session_id not in _sessions:
        _sessions[session_id] = {"client_id": None, "client_name": None, "last_lookup": []}
    return _sessions[session_id]


def set_client(session_id: str, client_id: int, client_name: str = ""):
    """Store the selected client for a session."""
    s = _get_or_create(session_id)
    s["client_id"] = client_id
    s["client_name"] = client_name


def get_client(session_id: str) -> dict | None:
    """Get the selected client for a session. Returns {"client_id": int, "client_name": str} or None."""
    s = _sessions.get(session_id)
    if s and s.get("client_id"):
        return {"client_id": s["client_id"], "client_name": s["client_name"]}
    return None


def set_last_lookup(session_id: str, clients: list[dict]):
    """Store the most recent lookup results for a session."""
    s = _get_or_create(session_id)
    s["last_lookup"] = clients


def get_last_lookup(session_id: str) -> list[dict]:
    """Get the most recent lookup results for a session."""
    s = _sessions.get(session_id)
    if s:
        return s.get("last_lookup", [])
    return []


def clear_client(session_id: str):
    """Clear the selected client for a session."""
    _sessions.pop(session_id, None)
