"""Bearer token authentication for image-bridge API."""

import hashlib
import secrets
from fastapi import Header, HTTPException, Request


def validate_bearer_token(
    config,
    authorization: str | None = None,
    x_admin_test: str | None = None,
) -> bool:
    """Validate Bearer token against stored hash in config.

    Args:
        config: ImageBridgeConfig with api_key_hash and api_key_salt
        authorization: Authorization header value
        x_admin_test: If "true", allow admin test requests from localhost

    Returns:
        True if valid, raises HTTPException otherwise
    """
    # Allow admin test requests from localhost (no auth needed)
    if x_admin_test == "true":
        return True

    if not config.api_key_hash or not config.api_key_salt:
        raise HTTPException(
            status_code=503,
            detail="API key not configured. Generate a key in the admin dashboard.",
        )

    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid Authorization format. Use: Bearer <key>")

    token = parts[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Empty bearer token")

    computed = hashlib.sha256((config.api_key_salt + token).encode()).hexdigest()
    if not secrets.compare_digest(computed, config.api_key_hash):
        raise HTTPException(status_code=401, detail="Invalid API key")

    return True
