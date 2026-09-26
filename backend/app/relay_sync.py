"""Edge <-> cloud relay synchronisation (queue based; the edge is the source of truth).

The edge server is behind NAT, so it initiates everything:

  push:  POST {relay}/sync/push   — open sessions (masked plates, blurred thumbnails, quotes per
                                    duration), changed vehicle balances/history, new receipts,
                                    pass types and lot settings
  pull:  GET  {relay}/sync/pull?after=<cursor>  — customer actions queued on the relay
  ack:   POST {relay}/sync/ack    — messages applied (idempotent on msg id via relay_inbox)

Message kinds from the relay: SELF_PAY_PAID, DUES_PAID, PASS_PAID, DISPUTE, CONTACT_VERIFIED.
The relay confirms self-pay UPI payments with the gateway itself, so customers can pay while the
site's internet is down; the edge catches up on the next pull. See docs/ARCHITECTURE.md.
"""
from __future__ import annotations

import base64
import hashlib
import io
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import utcnow
from .domain import disputes, payments, plates
from .domain import sessions as sess_svc
from .domain.lookup import active_pass, get_tariff, site_tz
from .domain.passes import pass_dict
from .domain.settings import get_setting, set_setting
from .domain.tariff import calculate_charge
from .models import (AnprEvent, LedgerEntry, ParkingSession, Pass, PassType, Payment, PayMode, PayStatus, Receipt,
                     RelayInbox, SessionStatus, Setting, Vehicle)

log = logging.getLogger(__name__)


def phone_hash(phone: str) -> str:
    digits = "".join(c for c in phone if c.isdigit())[-10:]
    return hashlib.sha256((get_settings().relay_api_key + ":" + digits).encode()).hexdigest()


def blurred_thumb(rel: Optional[str]) -> Optional[str]:
    """Small JPEG of the plate crop with its middle blurred (privacy), base64."""
    if not rel:
        return None
    path = Path(get_settings().image_root) / rel
    if not path.is_file():
        return None
    try:
        from PIL import Image, ImageFilter

        im = Image.open(path).convert("RGB")
        im.thumbnail((200, 100))
        w, h = im.size
        box = (int(w * 0.3), 0, int(w * 0.8), h)
        im.paste(im.crop(box).filter(ImageFilter.GaussianBlur(6)), box)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=60)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:  # pragma: no cover - Pillow missing / bad image
        return None


def _state(db: Session, key: str, default: Any = None) -> Any:
    row = db.get(Setting, f"_relay_{key}")
    return row.value if row else default


def _set_state(db: Session, key: str, value: Any) -> None:
    set_setting(db, f"_relay_{key}", value)


def build_push(db: Session, since: Optional[datetime]) -> dict:
    now = utcnow()
    tz = site_tz()
    durations = get_setting(db, "duration_buttons")
    entries = []
    rows = db.execute(select(ParkingSession, Vehicle).join(Vehicle, ParkingSession.vehicle_id == Vehicle.id).where(
        ParkingSession.status.in_([SessionStatus.OPEN, SessionStatus.PREPAID]),
        ParkingSession.entry_at >= now - timedelta(hours=14))).all()
    for s, v in rows:
        t = get_tariff(db, s.vehicle_class, s.entry_at)
        quotes = {str(d): max(0, calculate_charge(s.vehicle_class, s.entry_at, s.entry_at + timedelta(minutes=d), t, tz)
                              + v.balance_paise) for d in durations}
        ev = db.get(AnprEvent, s.entry_event_id) if s.entry_event_id else None
        entries.append({"session_id": s.id, "plate": v.plate, "masked_plate": plates.mask(v.plate),
                        "vehicle_class": s.vehicle_class, "entry_at": s.entry_at.isoformat(), "status": s.status,
                        "gate_id": s.entry_gate, "quotes": quotes,
                        "thumb_b64": blurred_thumb((ev.images or {}).get("plate_crop")) if ev and since is None else None})
    vstmt = select(Vehicle)
    if since is not None:
        changed = select(LedgerEntry.vehicle_id).where(LedgerEntry.created_at >= since)
        vstmt = vstmt.where((Vehicle.last_seen >= since) | Vehicle.id.in_(changed))
    vehicles = []
    for v in db.scalars(vstmt.limit(5000)).all():
        p = active_pass(db, v.id, now)
        hist = []
        if v.phone:
            for s in db.scalars(select(ParkingSession).where(ParkingSession.vehicle_id == v.id)
                                .order_by(ParkingSession.id.desc()).limit(20)).all():
                hist.append({"entry_at": s.entry_at.isoformat() if s.entry_at else None,
                             "exit_at": s.exit_at.isoformat() if s.exit_at else None, "charge_paise": s.charge_paise,
                             "status": s.status})
        vehicles.append({"plate": v.plate, "vehicle_class": v.vehicle_class, "balance_paise": v.balance_paise,
                         "phone_hash": phone_hash(v.phone) if v.phone else None,
                         "pass": pass_dict(db, p) if p else None, "history": hist})
    rstmt = select(Receipt).where(Receipt.synced_to_relay.is_(False)).limit(2000)
    receipts = db.scalars(rstmt).all()
    return {
        "generated_at": now.isoformat(), "full": since is None, "entries": entries, "vehicles": vehicles,
        "receipts": [{"code": r.code, "number": r.number, "data": r.data, "created_at": r.created_at.isoformat()} for r in receipts],
        "receipt_ids": [r.id for r in receipts],
        "pass_types": [{"id": p.id, "vehicle_class": p.vehicle_class, "name": p.name, "period_unit": p.period_unit,
                        "period_value": p.period_value, "price_paise": p.price_paise}
                       for p in db.scalars(select(PassType).where(PassType.active.is_(True)))],
        "settings": {k: get_setting(db, k) for k in ("lot_name", "receipt_footer", "duration_buttons", "upi_vpa",
                                                     "upi_payee_name", "gstin")},
    }


# ------------------------------------------------------------------ inbound
def apply_message(db: Session, msg: dict) -> dict:
    """Apply one relay message exactly once."""
    if db.get(RelayInbox, msg["id"]) is not None:
        return {"id": msg["id"], "status": "already_applied"}
    kind, p = msg["kind"], msg.get("payload") or {}
    result: dict = {}
    if kind in ("SELF_PAY_PAID", "DUES_PAID", "PASS_PAID"):
        result = _apply_payment(db, kind, p)
    elif kind == "DISPUTE":
        v = sess_svc.find_vehicle(db, plates.normalise(p["plate"]))
        if v is None:
            result = {"error": "unknown plate"}
        else:
            d = disputes.raise_dispute(db, vehicle_id=v.id, raised_by_role="CUSTOMER", claimed_paise=p.get("claimed_paise"),
                                       claimed_mode=p.get("claimed_mode", "CASH"), claimed_when=p.get("claimed_when"),
                                       note=p.get("note"), client_uuid=f"relay-{msg['id']}")
            result = {"dispute_id": d.id}
    elif kind == "CONTACT_VERIFIED":
        v = sess_svc.find_vehicle(db, plates.normalise(p["plate"]))
        if v is not None and not v.phone:
            v.phone = "".join(c for c in p["phone"] if c.isdigit())[-10:]
        result = {"ok": v is not None}
    elif kind == "DATA_REQUEST":
        from .models import Alert

        a = Alert(kind="DATA_REQUEST", severity="INFO", message=f"Customer data request ({p.get('request_type', 'access')}) "
                  f"for {p.get('plate', '?')}", data={k: v for k, v in p.items() if k != "phone"})
        db.add(a)
        db.flush()
        result = {"alert_id": a.id}
    else:
        result = {"error": f"unknown kind {kind}"}
    db.add(RelayInbox(msg_id=msg["id"], kind=kind, payload=p, result=result))
    db.flush()
    return {"id": msg["id"], "status": "applied", **result}


def _apply_payment(db: Session, kind: str, p: dict) -> dict:
    existing = db.scalars(select(Payment).where(Payment.txn_ref == p["txn_ref"])).first()
    if existing is not None:
        return {"payment_id": existing.id, "duplicate": True}
    norm = plates.normalise(p["plate"])
    sess = db.get(ParkingSession, p["session_id"]) if p.get("session_id") else None
    vehicle = db.get(Vehicle, sess.vehicle_id) if sess else sess_svc.find_vehicle(db, norm)
    if vehicle is None:
        vclass = p.get("vehicle_class", "BIKE")
        vehicle = sess_svc.get_or_create_vehicle(db, norm, vclass, utcnow())
    pass_id = None
    purpose = {"SELF_PAY_PAID": "SESSION", "DUES_PAID": "DUES", "PASS_PAID": "PASS"}[kind]
    if kind == "PASS_PAID":
        pp = payments.create_pending_pass(db, vehicle.id, int(p["pass_type_id"]), user_id=None, channel="SELF_PAY")
        pass_id = pp.id
    paid_at = datetime.fromisoformat(p["paid_at"]) if p.get("paid_at") else utcnow()
    pay = Payment(vehicle_id=vehicle.id, session_id=sess.id if sess else None, pass_id=pass_id, purpose=purpose,
                  mode=PayMode.UPI, amount_paise=int(p["amount_paise"]), base_paise=int(p.get("base_paise", 0)),
                  dues_paise=int(p.get("dues_paise", 0)), duration_minutes=p.get("duration_minutes"), channel="SELF_PAY",
                  txn_ref=p["txn_ref"], gateway_ref=p.get("gateway_ref"), utr=p.get("utr"), status=PayStatus.INITIATED,
                  phone=p.get("phone"), created_at=paid_at)
    db.add(pay)
    db.flush()
    if p.get("phone") and not vehicle.phone:
        vehicle.phone = "".join(c for c in p["phone"] if c.isdigit())[-10:]
    if sess is not None and p.get("duration_minutes"):
        sess.est_duration_minutes = p["duration_minutes"]
    payments.confirm_payment(db, pay, gateway_ref=p.get("gateway_ref"), utr=p.get("utr"), at=paid_at,
                             note="self-pay via relay")
    return {"payment_id": pay.id}


# ------------------------------------------------------------------ loop
class RelayClient:
    def __init__(self, base_url: str, key: str, timeout: float = 15.0):
        self.client = httpx.Client(base_url=base_url.rstrip("/"), headers={"X-Relay-Key": key}, timeout=timeout)

    def push(self, body: dict) -> None:
        self.client.post("/sync/push", json=body).raise_for_status()

    def pull(self, after: Optional[str]) -> dict:
        r = self.client.get("/sync/pull", params={"after": after or ""})
        r.raise_for_status()
        return r.json()

    def ack(self, ids: list[str]) -> None:
        self.client.post("/sync/ack", json={"ids": ids}).raise_for_status()


def sync_once(db: Session, client: RelayClient) -> dict:
    """Pull and apply inbound messages first (money), then push state."""
    cursor = _state(db, "cursor")
    data = client.pull(cursor)
    applied = []
    for msg in data.get("messages", []):
        try:
            applied.append(apply_message(db, msg))
            db.commit()
        except Exception:
            db.rollback()
            log.exception("relay message %s failed", msg.get("id"))
            break
    if applied:
        client.ack([a["id"] for a in applied])
    if data.get("cursor"):
        _set_state(db, "cursor", data["cursor"])
    last = _state(db, "last_push")
    full_due = _state(db, "last_full") is None or (
        utcnow() - datetime.fromisoformat(_state(db, "last_full"))) > timedelta(minutes=10)
    since = None if full_due else (datetime.fromisoformat(last) if last else None)
    started = utcnow()
    body = build_push(db, since)
    receipt_ids = body.pop("receipt_ids")
    client.push(body)
    for r in db.scalars(select(Receipt).where(Receipt.id.in_(receipt_ids))).all():
        r.synced_to_relay = True
    _set_state(db, "last_push", started.isoformat())
    if since is None:
        _set_state(db, "last_full", started.isoformat())
    db.commit()
    return {"applied": len(applied), "pushed_entries": len(body["entries"]), "pushed_vehicles": len(body["vehicles"])}
