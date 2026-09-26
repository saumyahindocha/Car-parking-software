"""Customer pages: landing, self-pay, dues & balance, pass, receipts, privacy."""
from __future__ import annotations

import base64
import binascii
from datetime import datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import otp as otp_svc
from . import payments as pay
from .db import utcnow
from .deps import get_db, settings_of
from .gateway import MockGateway
from .models import Entry, PassType, PaymentIntent, Receipt, Vehicle
from .plates import approx_matches, display, mask, normalise, plausible
from .security import client_ip, edge_phone_hash, rate_hit, sign, unsign, valid_mobile, verified_form
from .sms import mask_phone
from .sync import deliver_direct_and_record, enqueue
from .web import AUTH_COOKIE, OTP_COOKIE, auth_of, otp_state, qr_svg, render, set_auth, set_cookie

router = APIRouter()
NEXT_PAGES = {"dues": "/dues/me", "pass": "/pass/me", "privacy": "/privacy/request"}


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _queue_direct(request: Request, bg: BackgroundTasks, msgs) -> None:
    if msgs and request.app.state.settings.edge_url:
        bg.add_task(deliver_direct_and_record, request.app.state, [m.id for m in msgs])


def _error(request: Request, db: Optional[Session], message: str, status: int = 400, back: str = "/"):
    return render(request, db, "message.html", {"title_key": "error", "message": message, "back": back}, status)


# ------------------------------------------------------------------ landing
@router.get("/")
def index(request: Request, db: Session = Depends(get_db)):
    return render(request, db, "index.html")


# ------------------------------------------------------------------ self-pay
def _recent_entries(db: Session, hours: int, limit: int) -> list[Entry]:
    since = utcnow() - timedelta(hours=hours)
    return list(db.scalars(select(Entry).where(Entry.entry_at >= since).order_by(Entry.entry_at.desc())
                           .limit(limit)).all())


@router.get("/pay")
def pay_list(request: Request, db: Session = Depends(get_db)):
    s = settings_of(request)
    entries = _recent_entries(db, s.recent_hours, s.recent_limit)
    paid = {e.session_id for e in entries if pay.entry_is_paid(db, e)}
    return render(request, db, "pay_list.html", {"entries": entries, "paid": paid, "hours": s.recent_hours})


@router.post("/pay/find")
async def pay_find(request: Request, db: Session = Depends(get_db)):
    s = settings_of(request)
    form = await verified_form(request)
    typed = normalise(str(form.get("plate", "")))
    if not rate_hit(db, s.secret_key, "search-ip", client_ip(request, s.trust_proxy_headers), s.search_per_ip,
                    s.search_per_ip_window_s):
        db.commit()
        return _error(request, db, "Too many searches. Please wait a few minutes.", 429, "/pay")
    db.commit()
    if not plausible(typed):
        return render(request, db, "pay_list.html", {"entries": [], "paid": set(), "hours": s.recent_hours,
                                                     "search_error": True, "typed": typed}, 400)
    open_entries = db.scalars(select(Entry).order_by(Entry.entry_at.desc())).all()
    exact = [e for e in open_entries if e.plate == typed]
    if exact:
        return _entry_page(request, db, exact[0], revealed=True)
    near = approx_matches(typed, {e.plate for e in open_entries})
    cands = [e for p, _ in near[:5] for e in open_entries if e.plate == p]
    return render(request, db, "pay_find.html", {"typed": display(typed), "candidates": cands})


def _entry_page(request: Request, db: Session, entry: Entry, revealed: bool, status: int = 200):
    s = settings_of(request)
    veh = db.get(Vehicle, entry.plate)
    options = sorted(((int(k), v) for k, v in entry.quotes.items()), key=lambda x: x[0])
    dues = max(0, veh.balance_paise) if veh else 0
    reveal = sign(s.secret_key, {"sid": entry.session_id}, 1800, "reveal") if revealed else ""
    return render(request, db, "pay_entry.html", {
        "entry": entry, "plate_shown": display(entry.plate) if revealed else entry.masked_plate, "options": options,
        "dues": dues, "already_paid": pay.entry_is_paid(db, entry), "reveal": reveal}, status)


@router.get("/pay/s/{sid}")
def pay_entry(sid: int, request: Request, db: Session = Depends(get_db)):
    entry = db.get(Entry, sid)
    if entry is None:
        return _error(request, db, "This vehicle is no longer listed. It may have exited already.", 404, "/pay")
    return _entry_page(request, db, entry, revealed=False)


@router.get("/t/{sid}.jpg")
def thumb(sid: int, db: Session = Depends(get_db)):
    entry = db.get(Entry, sid)
    if entry is None or not entry.thumb_b64:
        raise HTTPException(404)
    try:
        data = base64.b64decode(entry.thumb_b64)
    except (binascii.Error, ValueError):
        raise HTTPException(404)
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=600"})


@router.post("/pay/start")
async def pay_start(request: Request, bg: BackgroundTasks, db: Session = Depends(get_db)):
    s = settings_of(request)
    form = await verified_form(request)
    try:
        sid, duration = int(form.get("sid", "")), int(form.get("duration", ""))
    except ValueError:
        return _error(request, db, "Please choose a duration.", 400, "/pay")
    entry = db.get(Entry, sid)
    if entry is None:
        return _error(request, db, "This vehicle is no longer listed. It may have exited already.", 404, "/pay")
    rv = unsign(s.secret_key, str(form.get("reveal", "")), "reveal")
    revealed = bool(rv and rv.get("sid") == sid)
    auth = auth_of(request)
    phone = auth["ph"] if auth and auth.get("plate") == entry.plate else None
    try:
        intent = pay.start_self_pay(db, request.app.state.gateway, entry, duration, qr_expiry_s=s.qr_expiry_s,
                                    phone=phone, plate_revealed=revealed)
    except pay.PaymentError as exc:
        db.rollback()
        return _error(request, db, str(exc), 400, f"/pay/s/{sid}")
    db.commit()
    return _redirect(f"/pay/i/{intent.token}")


# ------------------------------------------------------------------ payment status / QR
def _intent(db: Session, token: str) -> PaymentIntent:
    intent = db.scalars(select(PaymentIntent).where(PaymentIntent.token == token)).first()
    if intent is None:
        raise HTTPException(404, "payment not found")
    return intent


def _poll(request: Request, bg: BackgroundTasks, db: Session, intent: PaymentIntent) -> None:
    msgs = pay.poll_status(db, request.app.state.gateway, intent, settings_of(request).status_poll_min_s)
    db.commit()
    _queue_direct(request, bg, msgs)


def _intent_plate(intent: PaymentIntent) -> str:
    return display(intent.plate) if intent.plate_revealed else mask(intent.plate)


@router.get("/pay/i/{token}")
def intent_page(token: str, request: Request, bg: BackgroundTasks, db: Session = Depends(get_db)):
    intent = _intent(db, token)
    _poll(request, bg, db, intent)
    ctx: dict[str, Any] = {"intent": intent, "plate_shown": _intent_plate(intent)}
    if intent.status == "PAID":
        rec = pay.receipt_for(db, intent)
        pt = db.get(PassType, intent.pass_type_id) if intent.pass_type_id else None
        return render(request, db, "pay_done.html", ctx | {"receipt": rec, "pass_type": pt})
    if intent.status in ("EXPIRED", "FAILED"):
        return render(request, db, "message.html", {"title_key": "error", "message_key":
                      "qr_expired" if intent.status == "EXPIRED" else "pay_failed", "back": "/"})
    svg = qr_svg(intent.upi_uri) if intent.upi_uri else None
    mock = isinstance(request.app.state.gateway, MockGateway)
    return render(request, db, "pay_qr.html", ctx | {"qr": svg, "show_demo": settings_of(request).demo_mode and mock})


@router.get("/pay/i/{token}/status")
def intent_status(token: str, request: Request, bg: BackgroundTasks, db: Session = Depends(get_db)):
    intent = _intent(db, token)
    _poll(request, bg, db, intent)
    rec = pay.receipt_for(db, intent)
    return JSONResponse({"status": intent.status, "receipt_url": f"/r/{rec.code}" if rec else None},
                        headers={"Cache-Control": "no-store"})


@router.post("/pay/i/{token}/demo-pay")
async def demo_pay(token: str, request: Request, bg: BackgroundTasks, db: Session = Depends(get_db)):
    await verified_form(request)
    gw = request.app.state.gateway
    if not settings_of(request).demo_mode or not isinstance(gw, MockGateway):
        raise HTTPException(404)
    intent = _intent(db, token)
    if intent.status == "PENDING":
        body, headers = gw.webhook_body(gw.pay(intent.txn_ref))
        msgs = []
        for ev in gw.parse_webhook(body, headers):
            msgs += pay.apply_gateway_event(db, ev)[1]
        db.commit()
        _queue_direct(request, bg, msgs)
    return _redirect(f"/pay/i/{token}")


# ------------------------------------------------------------------ OTP verification
@router.get("/verify")
def verify_start(request: Request, next: str = "dues", plate: str = "", db: Session = Depends(get_db)):
    if next not in NEXT_PAGES:
        next = "dues"
    return render(request, db, "verify.html", {"next": next, "plate": normalise(plate)})


@router.post("/otp/send")
async def otp_send(request: Request, db: Session = Depends(get_db)):
    s = settings_of(request)
    form = await verified_form(request)
    nxt = str(form.get("next", "dues"))
    nxt = nxt if nxt in NEXT_PAGES else "dues"
    plate = normalise(str(form.get("plate", "")))
    phone = valid_mobile(str(form.get("phone", "")))
    ctx = {"next": nxt, "plate": plate}
    if not plausible(plate):
        return render(request, db, "verify.html", ctx | {"error": "Please enter a valid vehicle number."}, 400)
    if phone is None:
        return render(request, db, "verify.html", ctx | {"error": "Please enter a valid 10-digit mobile number."}, 400)
    try:
        issued = otp_svc.issue(db, s, request.app.state.sms, phone, client_ip(request, s.trust_proxy_headers))
    except otp_svc.OtpError as exc:
        db.commit()  # keep the rate-limit hits
        return render(request, db, "verify.html", ctx | {"error": str(exc)}, 429 if exc.code.startswith("rate") else 502)
    db.commit()
    demo_code = None
    sms = request.app.state.sms
    if s.demo_mode and hasattr(sms, "last_code"):
        demo_code = sms.last_code(phone)
    resp = render(request, db, "otp.html", {"phone_masked": mask_phone(phone), "demo_code": demo_code, "next": nxt})
    set_cookie(request, resp, OTP_COOKIE, sign(s.secret_key, {"oid": issued.otp_id, "ph": phone, "plate": plate,
                                                               "next": nxt}, s.otp_ttl_s, "otp"), s.otp_ttl_s)
    return resp


@router.post("/otp/verify")
async def otp_verify(request: Request, db: Session = Depends(get_db)):
    s = settings_of(request)
    form = await verified_form(request)
    st = otp_state(request)
    if not st:
        return render(request, db, "verify.html", {"next": "dues", "plate": "",
                                                    "error": "The OTP has expired. Please request a new one."}, 400)
    try:
        otp_svc.verify(db, s, int(st["oid"]), st["ph"], str(form.get("code", "")))
    except otp_svc.OtpError as exc:
        db.commit()  # persist the failed attempt
        if exc.code == "wrong":
            return render(request, db, "otp.html", {"phone_masked": mask_phone(st["ph"]), "error": str(exc),
                                                    "next": st["next"]}, 400)
        return render(request, db, "verify.html", {"next": st["next"], "plate": st["plate"], "error": str(exc)}, 400)
    db.commit()
    resp = _redirect(NEXT_PAGES.get(st["next"], "/dues/me"))
    set_auth(request, resp, st["ph"], st["plate"])
    resp.delete_cookie(OTP_COOKIE, path="/")
    return resp


@router.get("/logout")
def logout():
    resp = _redirect("/")
    resp.delete_cookie(AUTH_COOKIE, path="/")
    return resp


def _access(request: Request, db: Session, auth: dict) -> tuple[Optional[Vehicle], str]:
    """'match' (phone on record matches), 'nophone' (no phone on record), 'mismatch', or 'unknown'."""
    veh = db.get(Vehicle, auth["plate"])
    if veh is None:
        return None, "unknown"
    if veh.phone_hash is None:
        return veh, "nophone"
    ok = veh.phone_hash == edge_phone_hash(settings_of(request).relay_api_key, auth["ph"])
    return veh, "match" if ok else "mismatch"


# ------------------------------------------------------------------ dues & balance
@router.get("/dues")
def dues_start(request: Request, db: Session = Depends(get_db)):
    if auth_of(request):
        return _redirect("/dues/me")
    return render(request, db, "verify.html", {"next": "dues", "plate": ""})


@router.get("/dues/me")
def dues_me(request: Request, db: Session = Depends(get_db)):
    auth = auth_of(request)
    if not auth:
        return _redirect("/verify?next=dues")
    veh, access = _access(request, db, auth)
    pending = pay.pending_relay_paid(db, veh.plate, veh.updated_at) if veh else 0
    return render(request, db, "dues.html", {
        "plate_shown": display(auth["plate"]), "vehicle": veh, "access": access,
        "due": pay.outstanding_dues(db, veh), "pending": pending,
        "history": (veh.history or []) if access == "match" else []})


@router.post("/dues/pay")
async def dues_pay(request: Request, db: Session = Depends(get_db)):
    s = settings_of(request)
    await verified_form(request)
    auth = auth_of(request)
    if not auth:
        return _redirect("/verify?next=dues")
    veh, access = _access(request, db, auth)
    if veh is None or access == "mismatch":
        return _error(request, db, "Dues cannot be paid online for this vehicle.", 403, "/dues/me")
    try:
        intent = pay.start_dues(db, request.app.state.gateway, veh, phone=auth["ph"], qr_expiry_s=s.qr_expiry_s)
    except pay.PaymentError as exc:
        db.rollback()
        return _error(request, db, str(exc), 400, "/dues/me")
    db.commit()
    return _redirect(f"/pay/i/{intent.token}")


@router.get("/dues/dispute")
def dispute_form(request: Request, db: Session = Depends(get_db)):
    auth = auth_of(request)
    if not auth:
        return _redirect("/verify?next=dues")
    veh, access = _access(request, db, auth)
    if access not in ("match", "nophone"):
        return _error(request, db, "A dispute can only be raised for a vehicle registered to your number.", 403,
                      "/dues/me")
    today = datetime.now(ZoneInfo(settings_of(request).site_timezone)).date().isoformat()
    return render(request, db, "dispute.html", {"plate_shown": display(auth["plate"]), "today": today,
                                                "due": pay.outstanding_dues(db, veh)})


@router.post("/dues/dispute")
async def dispute_submit(request: Request, bg: BackgroundTasks, db: Session = Depends(get_db)):
    s = settings_of(request)
    form = await verified_form(request)
    auth = auth_of(request)
    if not auth:
        return _redirect("/verify?next=dues")
    veh, access = _access(request, db, auth)
    if access not in ("match", "nophone"):
        return _error(request, db, "A dispute can only be raised for a vehicle registered to your number.", 403,
                      "/dues/me")
    if not rate_hit(db, s.secret_key, "dispute", auth["plate"], 5, 86400):
        db.commit()
        return _error(request, db, "Too many disputes today for this vehicle.", 429, "/dues/me")
    tz = ZoneInfo(s.site_timezone)
    try:
        day = datetime.strptime(str(form.get("date", "")), "%Y-%m-%d").date()
        tm = datetime.strptime(str(form.get("time", "") or "12:00"), "%H:%M").time()
        when = datetime.combine(day, tm, tzinfo=tz)
        amount = str(form.get("amount", "")).strip()
        claimed = int(round(float(amount) * 100)) if amount else None
    except ValueError:
        return _error(request, db, "Please check the date, time and amount.", 400, "/dues/dispute")
    if when > datetime.now(tz) + timedelta(hours=1) or when < datetime.now(tz) - timedelta(days=90):
        return _error(request, db, "Please enter a date within the last 90 days.", 400, "/dues/dispute")
    if claimed is not None and not (0 < claimed <= 10_000_00):
        return _error(request, db, "Please check the amount.", 400, "/dues/dispute")
    mode = "UPI" if str(form.get("mode", "CASH")).upper() == "UPI" else "CASH"
    note = str(form.get("note", "")).strip()[:500] or None
    msg = enqueue(db, "DISPUTE", {"plate": auth["plate"], "claimed_paise": claimed, "claimed_mode": mode,
                                  "claimed_when": when.isoformat(timespec="minutes"), "note": note})
    db.commit()
    _queue_direct(request, bg, [msg])
    return render(request, db, "message.html", {"title_key": "dispute_title", "message_key": "dispute_done",
                                                "back": "/dues/me", "ok": True})


# ------------------------------------------------------------------ passes
@router.get("/pass")
def pass_start(request: Request, plate: str = "", db: Session = Depends(get_db)):
    auth = auth_of(request)
    p = normalise(plate)
    if auth and (not p or p == auth["plate"]):
        return _redirect("/pass/me")
    return render(request, db, "verify.html", {"next": "pass", "plate": p})


@router.get("/pass/me")
def pass_me(request: Request, db: Session = Depends(get_db)):
    auth = auth_of(request)
    if not auth:
        return _redirect("/verify?next=pass")
    veh, access = _access(request, db, auth)
    stmt = select(PassType).where(PassType.active.is_(True)).order_by(PassType.vehicle_class, PassType.price_paise)
    if veh is not None:
        stmt = stmt.where(PassType.vehicle_class == veh.vehicle_class)
    types = db.scalars(stmt).all()
    current = veh.pass_ if veh is not None and access in ("match", "nophone") else None
    return render(request, db, "pass.html", {"plate_shown": display(auth["plate"]), "types": types,
                                             "current": current, "vehicle": veh})


@router.post("/pass/pay")
async def pass_pay(request: Request, db: Session = Depends(get_db)):
    s = settings_of(request)
    form = await verified_form(request)
    auth = auth_of(request)
    if not auth:
        return _redirect("/verify?next=pass")
    try:
        pt = db.get(PassType, int(form.get("pass_type_id", "")))
    except ValueError:
        pt = None
    veh, access = _access(request, db, auth)
    if pt is None or (veh is not None and pt.vehicle_class != veh.vehicle_class):
        return _error(request, db, "Please choose a pass.", 400, "/pass/me")
    try:
        intent = pay.start_pass(db, request.app.state.gateway, auth["plate"], pt, phone=auth["ph"],
                                qr_expiry_s=s.qr_expiry_s, contact_verified=access in ("nophone", "unknown"))
    except pay.PaymentError as exc:
        db.rollback()
        return _error(request, db, str(exc), 400, "/pass/me")
    db.commit()
    return _redirect(f"/pay/i/{intent.token}")


# ------------------------------------------------------------------ receipts
@router.get("/r/{code}")
def receipt_page(code: str, request: Request, db: Session = Depends(get_db)):
    rec = db.get(Receipt, code) if len(code) <= 32 else None
    if rec is None:
        return render(request, db, "receipt_missing.html", {"code": code}, 404)
    resp = render(request, db, "receipt.html", {"r": rec, "d": rec.data})
    resp.headers["X-Robots-Tag"] = "noindex"
    return resp


# ------------------------------------------------------------------ privacy (DPDP Act 2023)
@router.get("/privacy")
def privacy(request: Request, db: Session = Depends(get_db)):
    return render(request, db, "privacy.html", {"contact": settings_of(request).grievance_contact})


@router.get("/privacy/request")
def privacy_request_form(request: Request, db: Session = Depends(get_db)):
    auth = auth_of(request)
    if not auth:
        return _redirect("/verify?next=privacy")
    return render(request, db, "data_request.html", {"plate_shown": display(auth["plate"])})


@router.post("/privacy/request")
async def privacy_request(request: Request, bg: BackgroundTasks, db: Session = Depends(get_db)):
    form = await verified_form(request)
    auth = auth_of(request)
    if not auth:
        return _redirect("/verify?next=privacy")
    rtype = str(form.get("type", "ACCESS")).upper()
    if rtype not in ("ACCESS", "CORRECT", "DELETE", "WITHDRAW_CONSENT"):
        rtype = "ACCESS"
    s = settings_of(request)
    if not rate_hit(db, s.secret_key, "data-req", auth["ph"], 3, 86400):
        db.commit()
        return _error(request, db, "You have already sent requests today.", 429, "/privacy")
    msg = enqueue(db, "DATA_REQUEST", {"plate": auth["plate"], "phone": auth["ph"], "request_type": rtype,
                                       "note": str(form.get("note", "")).strip()[:1000] or None,
                                       "requested_at": utcnow().isoformat()})
    db.commit()
    _queue_direct(request, bg, [msg])
    return render(request, db, "message.html", {"title_key": "data_request_title", "message_key": "request_done",
                                                "back": "/", "ok": True})
