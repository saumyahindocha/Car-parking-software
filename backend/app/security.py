"""Password/PIN hashing (PBKDF2-SHA256) and signed bearer tokens (HMAC-SHA256), stdlib only."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import json
import secrets
import time
from typing import Any, Optional

from .config import get_settings

_ITER = int(os.environ.get("PARK_PBKDF2_ITERATIONS", "200000"))


def hash_secret(secret: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", secret.encode(), salt, _ITER)
    return f"pbkdf2${_ITER}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_secret(secret: str, stored: Optional[str]) -> bool:
    if not stored:
        return False
    try:
        _, it, salt, dk = stored.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", secret.encode(), base64.b64decode(salt), int(it))
        return hmac.compare_digest(calc, base64.b64decode(dk))
    except (ValueError, TypeError):
        return False


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_token(claims: dict[str, Any], ttl_s: Optional[int] = None) -> str:
    s = get_settings()
    body = dict(claims)
    body["exp"] = int(time.time()) + (ttl_s or s.token_ttl_hours * 3600)
    payload = _b64(json.dumps(body, separators=(",", ":")).encode())
    sig = _b64(hmac.new(s.secret_key.encode(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def read_token(token: str) -> Optional[dict[str, Any]]:
    try:
        payload, sig = token.split(".")
    except ValueError:
        return None
    expected = _b64(hmac.new(get_settings().secret_key.encode(), payload.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        return None
    claims = json.loads(_unb64(payload))
    if claims.get("exp", 0) < time.time():
        return None
    return claims
