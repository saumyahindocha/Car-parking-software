"""Auth, ANPR ingest, device heartbeats, images, bootstrap data for apps."""
from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import utcnow
from ..domain import sessions as sess_svc
from ..domain.lookup import gate_direction
from ..domain.settings import all_settings, get_setting
from ..models import Camera, Device, Gate, PassType, Role, Tariff, User, VehicleClass, Zone, ZoneAssignment
from ..security import make_token, read_token, verify_secret
from .deps import anpr_key, current_user, device_key, get_db, staff
from .serial import iso

router = APIRouter(prefix="/api")


# ------------------------------------------------------------------ auth
class LoginIn(BaseModel):
    username: str
    password: Optional[str] = None
    pin: Optional[str] = None
    device_id: Optional[str] = None


def user_json(u: User) -> dict:
    return {"id": u.id, "username": u.username, "name": u.name, "role": u.role, "phone": u.phone,
            "device_bound": bool(u.device_id), "active": u.active}


@router.post("/auth/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    u = db.scalars(select(User).where(User.username == body.username)).first()
    ok = u is not None and u.active and (
        (body.password and verify_secret(body.password, u.password_hash)) or
        (body.pin and verify_secret(body.pin, u.pin_hash)))
    if not ok:
        raise HTTPException(401, "invalid credentials")
    if u.role in (Role.WORKER, Role.GUARD, Role.SUPERVISOR) and body.device_id:
        if u.device_id and u.device_id != body.device_id:
            raise HTTPException(403, "this account is bound to another phone; ask the admin to reset it")
        if not u.device_id:
            u.device_id = body.device_id
            db.info["user_id"] = u.id
            db.commit()
    elif u.role in (Role.WORKER, Role.GUARD) and u.device_id and not body.device_id:
        raise HTTPException(403, "log in from your registered phone")
    token = make_token({"uid": u.id, "role": u.role, "dev": body.device_id})
    return {"token": token, "user": user_json(u)}


@router.get("/auth/me")
def me(user: User = Depends(current_user)):
    return user_json(user)


# ------------------------------------------------------------------ ANPR
class AnprEventIn(BaseModel):
    event_id: str
    gate_id: str
    camera_ids: list[str] = Field(default_factory=list)
    direction: str
    wrong_way: bool = False
    vehicle_class: str = "BIKE"
    ts_ms: int
    status: str = "READ"
    plate: Optional[str] = None
    confidence: float = 0.0
    candidates: list[dict] = Field(default_factory=list)
    images: dict[str, Any] = Field(default_factory=dict)
    images_b64: Optional[dict[str, str]] = None
    latency_ms: Optional[int] = None


def _store_b64_images(ev: AnprEventIn) -> None:
    if not ev.images_b64:
        return
    root = Path(get_settings().image_root)
    for kind, b64 in ev.images_b64.items():
        rel = f"upload/{ev.gate_id}/{ev.event_id}_{kind}.jpg"
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(b64))
        ev.images[kind] = rel


@router.post("/anpr/events")
def anpr_event(body: AnprEventIn, db: Session = Depends(get_db), _: str = Depends(anpr_key)):
    _store_b64_images(body)
    payload = body.model_dump(exclude={"images_b64"})
    res = sess_svc.ingest_event(db, payload)
    db.commit()
    return {"status": "accepted", "event_id": res.event.id, "anpr_status": res.event.status,
            "session_id": res.event.session_id, "duplicate_delivery": "duplicate_delivery" in res.notes}


@router.get("/anpr/config")
def anpr_config(db: Session = Depends(get_db), _: str = Depends(device_key)):
    now = utcnow()
    gates = []
    for g in db.scalars(select(Gate).where(Gate.enabled.is_(True)).order_by(Gate.id)).all():
        gates.append({"id": g.id, "name": g.name, "direction": gate_direction(g, now), "configured_direction": g.direction,
                      "schedule": g.schedule,
                      "cameras": [{"id": c.id, "role": c.role, "side": c.side, "rtsp_url": c.rtsp_url, "roi": c.roi,
                                   "capture_line": c.capture_line, "in_vector": c.in_vector}
                                  for c in g.cameras if c.enabled]})
    classes = {vc.code: vc.enabled for vc in db.scalars(select(VehicleClass)).all()}
    return {"gates": gates, "vehicle_classes": classes,
            "settings": {"merge_window_s": get_setting(db, "merge_window_seconds"),
                         "dedupe_window_s": get_setting(db, "dedupe_seconds"),
                         "min_confidence": get_setting(db, "min_confidence"),
                         "state_codes": get_setting(db, "state_codes")}}


# ------------------------------------------------------------------ devices
class HeartbeatIn(BaseModel):
    device_id: str
    kind: str = "CAMERA"
    gate_id: Optional[str] = None
    name: Optional[str] = None
    metrics: dict[str, Any] = Field(default_factory=dict)


@router.post("/devices/heartbeat")
def heartbeat(body: HeartbeatIn, db: Session = Depends(get_db), _: str = Depends(device_key)):
    d = db.get(Device, body.device_id)
    if d is None:
        d = Device(id=body.device_id, kind=body.kind, gate_id=body.gate_id, name=body.name)
        db.add(d)
    d.kind, d.gate_id, d.last_seen, d.metrics = body.kind, body.gate_id or d.gate_id, utcnow(), body.metrics
    if body.name:
        d.name = body.name
    d.stalled_alerted = False if body.metrics.get("stream_ok", True) else d.stalled_alerted
    db.commit()
    return {"ok": True, "server_time": utcnow().isoformat()}


@router.get("/devices")
def devices(db: Session = Depends(get_db), user: User = Depends(staff)):
    now = utcnow()
    out = []
    for d in db.scalars(select(Device).order_by(Device.kind, Device.id)).all():
        age = (now - d.last_seen).total_seconds() if d.last_seen else None
        out.append({"id": d.id, "kind": d.kind, "gate_id": d.gate_id, "name": d.name, "last_seen": iso(d.last_seen),
                    "age_s": age, "online": age is not None and age < 60, "metrics": d.metrics})
    return out


# ------------------------------------------------------------------ images (role-based)
@router.get("/images/{path:path}")
def image(path: str, request: Request, db: Session = Depends(get_db)):
    s = get_settings()
    auth = request.headers.get("authorization", "")
    tok = auth[7:] if auth.lower().startswith("bearer ") else request.query_params.get("token", "")
    key = request.headers.get("x-device-key") or request.query_params.get("key")
    claims = read_token(tok) if tok else None
    if not claims and key not in (s.anpr_api_key, s.device_api_key):
        raise HTTPException(401, "not authenticated")
    if claims and claims.get("role") not in Role.ALL:
        raise HTTPException(403, "no image access")
    root = Path(s.image_root).resolve()
    full = (root / path).resolve()
    if root not in full.parents or not full.is_file():
        raise HTTPException(404, "image not found")
    return FileResponse(full, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


# ------------------------------------------------------------------ bootstrap for apps (cached offline)
@router.get("/bootstrap")
def bootstrap(db: Session = Depends(get_db), user: User = Depends(current_user)):
    now = utcnow()
    za = db.scalars(select(ZoneAssignment).where(ZoneAssignment.user_id == user.id, ZoneAssignment.starts_at <= now,
                                                 ZoneAssignment.ends_at > now)).first()
    zone = db.get(Zone, za.zone_id) if za else None
    st = all_settings(db)
    tariffs = db.scalars(select(Tariff).order_by(Tariff.vehicle_class, Tariff.version)).all()
    return {
        "user": user_json(user),
        "zone": {"id": zone.id, "name": zone.name, "until": iso(za.ends_at)} if zone else None,
        "zones": [{"id": z.id, "name": z.name, "gate_id": z.gate_id} for z in db.scalars(select(Zone)).all()],
        "server_time": now.isoformat(),
        "site_timezone": get_settings().site_timezone,
        "public_receipt_base": get_settings().public_receipt_base.rstrip("/") or None,
        "settings": {k: st[k] for k in ("lot_name", "upi_vpa", "upi_payee_name", "cash_enabled", "cash_desk_only",
                                        "cash_desk_user_ids", "cash_limit_paise", "cash_warn_ratio", "duration_buttons",
                                        "approx_tolerance", "state_codes", "to_collect_hours", "receipt_footer",
                                        "pass_candidate_visits", "alert_balance_threshold_paise")},
        "cash_allowed": bool(st["cash_enabled"]) and (not st["cash_desk_only"] or user.id in (st["cash_desk_user_ids"] or [])),
        "vehicle_classes": [{"code": v.code, "name": v.name, "enabled": v.enabled} for v in db.scalars(select(VehicleClass))],
        "tariffs": [{"id": t.id, "vehicle_class": t.vehicle_class, "version": t.version, "effective_from": iso(t.effective_from),
                     "first_slab_minutes": t.first_slab_minutes, "first_slab_paise": t.first_slab_paise,
                     "per_hour_paise": t.per_hour_paise, "grace_minutes": t.grace_minutes, "block_minutes": t.block_minutes,
                     "block_cap_paise": t.block_cap_paise, "daily_cap_paise": t.daily_cap_paise,
                     "overnight_paise": t.overnight_paise, "overnight_cutoff_hour": t.overnight_cutoff_hour,
                     "free_minutes": t.free_minutes} for t in tariffs],
        "pass_types": [{"id": p.id, "vehicle_class": p.vehicle_class, "name": p.name, "period_unit": p.period_unit,
                        "period_value": p.period_value, "price_paise": p.price_paise, "is_default": p.is_default}
                       for p in db.scalars(select(PassType).where(PassType.active.is_(True)))],
        "gates": [{"id": g.id, "name": g.name} for g in db.scalars(select(Gate))],
    }


@router.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(select(1))
    return {"ok": True, "time": utcnow().isoformat(), "pid": os.getpid()}
