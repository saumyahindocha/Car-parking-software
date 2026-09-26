"""Rendering helpers shared by the page routes: templates, filters, cookies, QR codes."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from fastapi import Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .db import utcnow
from .i18n import LANGS, translate
from .security import CSRF_FIELD, csrf_token_for, sign, unsign
from .sync import kv_get, lot_settings

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
AUTH_COOKIE = "cw_auth"
OTP_COOKIE = "cw_otp"


def rupees(paise: Any) -> str:
    p = int(paise or 0)
    sign_ = "-" if p < 0 else ""
    whole, frac = divmod(abs(p), 100)
    s = str(whole)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups) + "," + tail
    return f"{sign_}₹{s}" if frac == 0 else f"{sign_}₹{s}.{frac:02d}"


def make_localtime(tzname: str):
    tz = ZoneInfo(tzname)

    def localtime(v: Any, fmt: str = "%d %b %Y, %I:%M %p") -> str:
        if not v:
            return "—"
        if isinstance(v, str):
            try:
                v = datetime.fromisoformat(v)
            except ValueError:
                return v
        if v.tzinfo is None:
            from datetime import timezone

            v = v.replace(tzinfo=timezone.utc)
        return v.astimezone(tz).strftime(fmt).replace("AM", "am").replace("PM", "pm")

    return localtime


def duration_label(lang: str, minutes: Any) -> str:
    m = int(minutes or 0)
    if m == 1440:
        return translate(lang, "day")
    h = m / 60
    return translate(lang, "hours", n=int(h) if h == int(h) else round(h, 1))


def qr_svg(data: str) -> str:
    """Inline SVG QR (no image request: fast on 3G, works with a strict CSP)."""
    import qrcode
    import qrcode.image.svg

    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2,
                      error_correction=qrcode.constants.ERROR_CORRECT_M)
    svg = img.to_string(encoding="unicode")
    if svg.startswith("<?xml"):
        svg = svg.split("?>", 1)[1]
    return svg.replace("<svg ", '<svg role="img" aria-label="UPI QR code" class="qr" ', 1)


def render(request: Request, db: Optional[Session], name: str, ctx: Optional[dict[str, Any]] = None,
           status: int = 200) -> HTMLResponse:
    s = request.app.state.settings
    lang = getattr(request.state, "lang", "en")
    lot = lot_settings(db) if db is not None else {"lot_name": "Station Parking", "receipt_footer": ""}
    stale = False
    if db is not None:
        lp = kv_get(db, "last_push_at")
        stale = not lp or (utcnow() - datetime.fromisoformat(lp)).total_seconds() > s.stale_push_warn_s
    base = {
        "request": request, "lang": lang, "langs": LANGS, "csrf": csrf_token_for(request), "csrf_field": CSRF_FIELD,
        "t": lambda key, **kw: translate(lang, key, **kw), "lot": lot, "demo": s.demo_mode, "stale": stale,
        "rupees": rupees, "localtime": make_localtime(s.site_timezone),
        "dur": lambda m: duration_label(lang, m), "authed": auth_of(request) is not None,
        "path": request.url.path,
    }
    base.update(ctx or {})
    return TEMPLATES.TemplateResponse(request, name, base, status_code=status)


# ------------------------------------------------------------------ cookies
def set_cookie(request: Request, resp: Response, name: str, value: str, max_age: int) -> None:
    resp.set_cookie(name, value, max_age=max_age, httponly=True, secure=request.app.state.settings.cookie_secure,
                    samesite="lax", path="/")


def auth_of(request: Request) -> Optional[dict[str, Any]]:
    """OTP-verified identity: {'ph': '9876543210', 'plate': 'MH12AB1234'} or None."""
    s = request.app.state.settings
    return unsign(s.secret_key, request.cookies.get(AUTH_COOKIE), "auth")


def set_auth(request: Request, resp: Response, phone: str, plate: str) -> None:
    s = request.app.state.settings
    set_cookie(request, resp, AUTH_COOKIE, sign(s.secret_key, {"ph": phone, "plate": plate}, s.auth_ttl_s, "auth"),
               s.auth_ttl_s)


def otp_state(request: Request) -> Optional[dict[str, Any]]:
    s = request.app.state.settings
    return unsign(s.secret_key, request.cookies.get(OTP_COOKIE), "otp")
