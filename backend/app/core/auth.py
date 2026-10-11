import hmac

from fastapi import Header, HTTPException

from app.core.config import settings


def require_key(x_access_key: str | None = Header(default=None)) -> None:
    """Guards costly routes. No ACCESS_KEY set = open (local dev)."""
    if not settings.access_key:
        return
    if not x_access_key or not hmac.compare_digest(x_access_key.strip(), settings.access_key):
        raise HTTPException(401, "Access key required. Use the Unlock box.")