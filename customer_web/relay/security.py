"""Relay key check, signed cookies, CSRF (double-submit cookie), hashing and DB-backed rate limits."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import timedelta
from typing import Any, Optional

from fastapi import HTTPException, Request
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .config import Settings
from .db import utcnow
from .models import RateEvent

CSRF_COOKIE = "cw_csrf"
CSRF_FIELD = "csrf_token"


def check_relay_key(settings: Settings, provided: Optional[str]) -> None:
    if not provided or not hmac.compare_digest(provided.encode(), settings.relay_api_key.encode()):
        raise HTTPException(401, "bad relay key")


def phone_digits(phone: str) -> str:
    return "".join(c for c in (phone or "") if c.isdigit())[-10:]


def valid_mobile(phone: str) -> Optional[str]:
    """Indian mobile: 10 digits starting 6-9, optional +91 / 0 prefix. Returns the 10 digits or None."""
    digits = "".join(c for c in (phone or "") if c.isdigit())
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10 and digits[0] in "6789":
        return digits
    return None


def edge_phone_hash(relay_key: str, phone: str) -> str:
    """Exactly backend/app/relay_sync.py phone_hash(): sha256(relay_key + ':' + last 10 digits)."""
    return hashlib.sha256((relay_key + ":" + phone_digits(phone)).encode()).hexdigest()


def keyed_hash(secret: str, *parts: str) -> str:
    return hmac.new(secret.encode(), "|".join(parts).encode(), hashlib.sha256).hexdigest()


# ------------------------------------------------------------------ signed cookies
def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign(secret: str, data: dict[str, Any], ttl_s: int, purpose: str) -> str:
    body = dict(data, _exp=int(time.time()) + ttl_s, _p=purpose)
    raw = _b64(json.dumps(body, separators=(",", ":")).encode())
    sig = _b64(hmac.new(secret.encode(), f"{purpose}.{raw}".encode(), hashlib.sha256).digest())
    return f"{raw}.{sig}"


def unsign(secret: str, token: Optional[str], purpose: str) -> Optional[dict[str, Any]]:
    if not token or "." not in token:
        return None
    raw, sig = token.rsplit(".", 1)
    good = _b64(hmac.new(secret.encode(), f"{purpose}.{raw}".encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, good):
        return None
    try:
        data = json.loads(_unb64(raw))
    except ValueError:
        return None
    if data.get("_p") != purpose or int(data.get("_exp", 0)) < time.time():
        return None
    return data


# ------------------------------------------------------------------ CSRF
def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_token_for(request: Request) -> str:
    tok = getattr(request.state, "csrf", None)
    if not tok:
        tok = request.cookies.get(CSRF_COOKIE) or new_csrf_token()
        request.state.csrf = tok
    return tok


async def verified_form(request: Request):
    """Parse a form POST and enforce the double-submit CSRF token (cookie == hidden field)."""
    form = await request.form()
    cookie = request.cookies.get(CSRF_COOKIE, "")
    field = str(form.get(CSRF_FIELD, ""))
    if not cookie or not field or not hmac.compare_digest(cookie, field):
        raise HTTPException(403, "Your session expired. Please go back, reload the page and try again.")
    return form


# ------------------------------------------------------------------ client IP + rate limits
def client_ip(request: Request, trust_proxy: bool) -> str:
    if trust_proxy:
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
        real = request.headers.get("x-real-ip")
        if real:
            return real.strip()
    return request.client.host if request.client else "unknown"


def rate_hit(db: Session, secret: str, bucket: str, ident: str, limit: int, window_s: int) -> bool:
    """Record one hit; False (and nothing recorded) if `limit` hits already happened in the window."""
    key = bucket + ":" + keyed_hash(secret, bucket, ident)[:40]
    since = utcnow() - timedelta(seconds=window_s)
    n = db.scalar(select(func.count(RateEvent.id)).where(RateEvent.key == key, RateEvent.created_at > since)) or 0
    if n >= limit:
        return False
    db.add(RateEvent(key=key))
    db.flush()
    return True


def purge_rate_events(db: Session, older_than_s: int = 86400) -> None:
    db.execute(delete(RateEvent).where(RateEvent.created_at < utcnow() - timedelta(seconds=older_than_s)))
