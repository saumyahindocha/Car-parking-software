"""Supervisor & admin endpoints: review queue, cash control, disputes, zones, configuration,
users, audit, privacy, reconciliation."""
from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..adapters.gateway import GatewayUnavailable, SettlementLine, get_gateway
from ..config import get_settings
from ..db import utcnow
from ..domain import cash, disputes, payments, privacy, reports
from ..domain import sessions as sess_svc
from ..domain.lookup import local_date
from ..domain.settings import DEFAULTS, all_settings, set_setting
from ..models import (AnprEvent, AuditLog, BankDeposit, Camera, CashHandover, EventStatus, Gate, ParkingSession, PassType,
                      Payment, PaymentDispute, PayStatus, Role, SessionStatus, Tariff, User, VehicleClass, Zone,
                      ZoneAssignment)
from ..security import hash_secret
from .deps import admin, get_db, supervisor
from .serial import event_json, iso, session_json

router = APIRouter(prefix="/api")


def _save_upload(f: Optional[UploadFile], sub: str) -> Optional[str]:
    if f is None:
        return None
    root = Path(get_settings().upload_root)
    ext = Path(f.filename or "x.jpg").suffix.lower() or ".jpg"
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".pdf"):
        raise HTTPException(400, "unsupported file type")
    rel = f"{sub}/{date.today():%Y/%m/%d}/{uuid.uuid4().hex}{ext}"
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(f.file.read())
    return rel


# ------------------------------------------------------------------ review queue
@router.get("/review")
def review_queue(db: Session = Depends(get_db), user: User = Depends(supervisor)):
    evs = db.scalars(select(AnprEvent).where(
        (AnprEvent.status.in_([EventStatus.UNREAD, EventStatus.REVIEW])) |
        ((AnprEvent.review_reason == "WORKER_CORRECTED") & AnprEvent.reviewed_at.is_(None)))
        .order_by(AnprEvent.ts.desc()).limit(300)).all()
    orphans = db.scalars(select(ParkingSession).where(ParkingSession.status.in_(
        [SessionStatus.ORPHAN_ENTRY, SessionStatus.ORPHAN_EXIT])).order_by(ParkingSession.id.desc()).limit(300)).all()
    claims = db.scalars(select(Payment).where(Payment.status == PayStatus.CLAIMED_OFFLINE).order_by(Payment.created_at)).all()
    stale_ids = {p.id for p in payments.stale_offline_claims(db)}
    dis = db.scalars(select(PaymentDispute).where(PaymentDispute.status == "OPEN").order_by(PaymentDispute.id)).all()
    return {
        "events": [event_json(e) for e in evs],
        "orphans": [session_json(db, s) for s in orphans],
        "offline_claims": [{**payments.payment_dict(db, p), "stale": p.id in stale_ids} for p in claims],
        "disputes": [disputes.dispute_dict(db, d) for d in dis],
        "unpaid_flagged": [session_json(db, s, with_images=False) for s in reports.unpaid_flagged_sessions(db)][:200],
        "counts": reports.review_queue_counts(db),
    }


class ResolveEventIn(BaseModel):
    plate: Optional[str] = None
    session_id: Optional[int] = None
    discard: bool = False
    confirm: bool = False  # for WORKER_CORRECTED items: accept the worker's correction
    note: Optional[str] = None


@router.post("/review/events/{event_id}/resolve")
def review_event(event_id: str, body: ResolveEventIn, db: Session = Depends(get_db), user: User = Depends(supervisor)):
    ev = db.get(AnprEvent, event_id)
    if ev is None:
        raise HTTPException(404, "event not found")
    if body.confirm and ev.review_reason == "WORKER_CORRECTED":
        ev.reviewed_by, ev.reviewed_at, ev.review_note = user.id, utcnow(), body.note or "correction confirmed"
        db.commit()
        return event_json(ev)
    res = sess_svc.resolve_event(db, event_id, user_id=user.id, plate=body.plate, session_id=body.session_id,
                                 discard=body.discard, note=body.note)
    db.commit()
    return {"event": event_json(res.event), "session": session_json(db, res.session) if res.session else None,
            "exit": res.exit_display}


class ResolveOrphanIn(BaseModel):
    action: str  # CHARGE | WAIVE
    at: Optional[datetime] = None
    note: str


@router.post("/review/sessions/{session_id}/resolve")
def review_orphan(session_id: int, body: ResolveOrphanIn, db: Session = Depends(get_db), user: User = Depends(supervisor)):
    s = sess_svc.resolve_orphan(db, session_id, user_id=user.id, action=body.action, at=body.at, note=body.note)
    db.commit()
    return session_json(db, s)


class ResolveClaimIn(BaseModel):
    action: str  # CONFIRM | FAIL
    utr: Optional[str] = None
    note: str


@router.post("/review/payments/{payment_id}/resolve")
def review_claim(payment_id: int, body: ResolveClaimIn, db: Session = Depends(get_db), user: User = Depends(supervisor)):
    p = db.get(Payment, payment_id)
    if p is None or p.status != PayStatus.CLAIMED_OFFLINE:
        raise HTTPException(400, "not an open offline claim")
    if body.action == "CONFIRM":
        if not body.utr:
            raise HTTPException(400, "UTR / bank reference required to confirm manually")
        payments.confirm_payment(db, p, utr=body.utr, note=f"manually confirmed by {user.name}: {body.note}")
    elif body.action == "FAIL":
        payments.fail_payment(db, p, f"not received ({user.name}): {body.note}")
    else:
        raise HTTPException(400, "action must be CONFIRM or FAIL")
    db.commit()
    return payments.payment_dict(db, p)


# ------------------------------------------------------------------ disputes
@router.get("/disputes")
def disputes_list(status: Optional[str] = None, worker_id: Optional[int] = None, db: Session = Depends(get_db),
                  user: User = Depends(supervisor)):
    stmt = select(PaymentDispute)
    if status:
        stmt = stmt.where(PaymentDispute.status == status)
    if worker_id:
        stmt = stmt.where(PaymentDispute.worker_id == worker_id)
    return [disputes.dispute_dict(db, d) for d in db.scalars(stmt.order_by(PaymentDispute.id.desc()).limit(500))]


class ResolveDisputeIn(BaseModel):
    outcome: str
    note: str
    adjust_paise: Optional[int] = None  # e.g. -1000 to credit an upheld cash claim


@router.post("/disputes/{dispute_id}/resolve")
def dispute_resolve(dispute_id: int, body: ResolveDisputeIn, db: Session = Depends(get_db), user: User = Depends(supervisor)):
    d = disputes.resolve_dispute(db, dispute_id, user, body.outcome, body.note)
    if body.adjust_paise:
        payments.adjust_balance(db, d.vehicle_id, body.adjust_paise, supervisor=user,
                                reason=f"dispute #{d.id} {body.outcome}: {body.note}")
    db.commit()
    return disputes.dispute_dict(db, d)


# ------------------------------------------------------------------ cash control
@router.get("/cash/holdings")
def holdings(db: Session = Depends(get_db), user: User = Depends(supervisor)):
    return cash.all_holdings(db)


@router.get("/cash/handovers")
def handovers(status: Optional[str] = "PENDING", db: Session = Depends(get_db), user: User = Depends(supervisor)):
    stmt = select(CashHandover)
    if status:
        stmt = stmt.where(CashHandover.status == status)
    return [cash.handover_dict(db, h) for h in db.scalars(stmt.order_by(CashHandover.id.desc()).limit(200))]


@router.post("/cash/handovers/{handover_id}/confirm")
def handover_confirm(handover_id: int, counted_denominations: str = Form(...), note: Optional[str] = Form(None),
                     photo: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(supervisor)):
    path = _save_upload(photo, "handovers")
    h = cash.confirm_handover(db, handover_id, user, json.loads(counted_denominations), photo_path=path, note=note)
    db.commit()
    return cash.handover_dict(db, h)


class RejectIn(BaseModel):
    note: str


@router.post("/cash/handovers/{handover_id}/reject")
def handover_reject(handover_id: int, body: RejectIn, db: Session = Depends(get_db), user: User = Depends(supervisor)):
    h = cash.reject_handover(db, handover_id, user, body.note)
    db.commit()
    return cash.handover_dict(db, h)


@router.post("/cash/deposits")
def deposit_create(business_date: str = Form(...), amount_paise: int = Form(...), slip_ref: str = Form(...),
                   note: Optional[str] = Form(None), slip_photo: UploadFile = File(...),
                   db: Session = Depends(get_db), user: User = Depends(supervisor)):
    path = _save_upload(slip_photo, "deposits")
    d = cash.record_deposit(db, user, business_date, amount_paise, slip_ref, path, note)
    db.commit()
    return {"id": d.id, "business_date": d.business_date, "amount_paise": d.amount_paise, "slip_ref": d.slip_ref,
            "bank_status": d.bank_status}


@router.get("/cash/deposits")
def deposit_list(db: Session = Depends(get_db), user: User = Depends(supervisor)):
    return [{"id": d.id, "business_date": d.business_date, "amount_paise": d.amount_paise, "slip_ref": d.slip_ref,
             "slip_photo_path": d.slip_photo_path, "deposited_by": d.deposited_by, "deposited_at": iso(d.deposited_at),
             "bank_status": d.bank_status, "bank_credited_paise": d.bank_credited_paise, "note": d.note}
            for d in db.scalars(select(BankDeposit).order_by(BankDeposit.id.desc()).limit(100))]


class BankCreditIn(BaseModel):
    credited_paise: int
    note: Optional[str] = None


@router.post("/cash/deposits/{deposit_id}/bank-credit")
def deposit_credit(deposit_id: int, body: BankCreditIn, db: Session = Depends(get_db), user: User = Depends(supervisor)):
    d = cash.mark_bank_credit(db, deposit_id, body.credited_paise, body.note)
    db.commit()
    return {"id": d.id, "bank_status": d.bank_status, "bank_credited_paise": d.bank_credited_paise}


@router.get("/cash/reconciliation")
def cash_recon(date: Optional[str] = None, db: Session = Depends(get_db), user: User = Depends(supervisor)):
    return cash.cash_reconciliation(db, date or local_date(utcnow()))


# ------------------------------------------------------------------ supervisor money actions
class ReasonIn(BaseModel):
    reason: str
    amount_paise: Optional[int] = None


@router.post("/payments/{payment_id}/reverse")
def reverse(payment_id: int, body: ReasonIn, db: Session = Depends(get_db), user: User = Depends(supervisor)):
    p = payments.reverse_cash(db, payment_id, supervisor=user, reason=body.reason)
    db.commit()
    return payments.payment_dict(db, p)


@router.post("/payments/{payment_id}/refund")
def refund(payment_id: int, body: ReasonIn, db: Session = Depends(get_db), user: User = Depends(supervisor)):
    p = payments.refund_upi(db, payment_id, supervisor=user, reason=body.reason, amount_paise=body.amount_paise)
    db.commit()
    return payments.payment_dict(db, p)


class AdjustIn(BaseModel):
    amount_paise: int
    reason: str


@router.post("/vehicles/{vehicle_id}/adjust")
def adjust(vehicle_id: int, body: AdjustIn, db: Session = Depends(get_db), user: User = Depends(supervisor)):
    payments.adjust_balance(db, vehicle_id, body.amount_paise, supervisor=user, reason=body.reason)
    db.commit()
    from ..models import Vehicle

    return {"vehicle_id": vehicle_id, "balance_paise": db.get(Vehicle, vehicle_id).balance_paise}


# ------------------------------------------------------------------ UPI reconciliation
@router.post("/reconciliation/upi/run")
def upi_recon_run(date: Optional[str] = None, db: Session = Depends(get_db), user: User = Depends(supervisor)):
    d = date or local_date(utcnow())
    try:
        lines = get_gateway().fetch_settlements(datetime.fromisoformat(d).date())
    except GatewayUnavailable as e:
        raise HTTPException(503, f"gateway unreachable: {e}")
    rep = payments.reconcile_upi(db, lines, day=d)
    db.commit()
    return {"date": d, "lines": len(lines), **rep}


@router.post("/reconciliation/upi/upload")
def upi_recon_upload(file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(supervisor)):
    """Bank statement / settlement CSV with columns txn_ref, amount (rupees), utr."""
    text = file.file.read().decode("utf-8-sig")
    lines = []
    for row in csv.DictReader(io.StringIO(text)):
        ref = (row.get("txn_ref") or row.get("reference") or row.get("tr") or "").strip()
        if not ref:
            continue
        amt = row.get("amount_paise") or None
        paise = int(amt) if amt else int(round(float(row.get("amount", "0").replace(",", "")) * 100))
        lines.append(SettlementLine(ref, paise, None, (row.get("utr") or "").strip() or None, None, "BANK"))
    rep = payments.reconcile_upi(db, lines)
    db.commit()
    return {"lines": len(lines), **rep}


# ------------------------------------------------------------------ zones
class ZoneIn(BaseModel):
    name: str
    gate_id: Optional[str] = None
    description: Optional[str] = None


@router.get("/zones")
def zones(db: Session = Depends(get_db), user: User = Depends(supervisor)):
    return [{"id": z.id, "name": z.name, "gate_id": z.gate_id, "description": z.description}
            for z in db.scalars(select(Zone).order_by(Zone.id))]


@router.post("/zones")
def zone_create(body: ZoneIn, db: Session = Depends(get_db), user: User = Depends(admin)):
    z = Zone(**body.model_dump())
    db.add(z)
    db.commit()
    return {"id": z.id}


@router.put("/zones/{zone_id}")
def zone_update(zone_id: int, body: ZoneIn, db: Session = Depends(get_db), user: User = Depends(admin)):
    z = db.get(Zone, zone_id)
    for k, v in body.model_dump().items():
        setattr(z, k, v)
    db.commit()
    return {"id": z.id}


class AssignIn(BaseModel):
    zone_id: int
    user_id: int
    starts_at: datetime
    ends_at: datetime
    shift_label: str = ""


@router.post("/zones/assignments")
def assign(body: AssignIn, db: Session = Depends(get_db), user: User = Depends(supervisor)):
    if body.ends_at <= body.starts_at:
        raise HTTPException(400, "end must be after start")
    a = ZoneAssignment(**body.model_dump(), assigned_by=user.id)
    db.add(a)
    db.commit()
    return {"id": a.id}


@router.get("/zones/assignments")
def assignments(date: Optional[str] = None, db: Session = Depends(get_db), user: User = Depends(supervisor)):
    from ..domain.lookup import day_bounds

    s, e = day_bounds(date or local_date(utcnow()))
    rows = db.scalars(select(ZoneAssignment).where(ZoneAssignment.starts_at < e, ZoneAssignment.ends_at > s)
                      .order_by(ZoneAssignment.starts_at)).all()
    return [{"id": a.id, "zone_id": a.zone_id, "user_id": a.user_id, "user_name": db.get(User, a.user_id).name,
             "starts_at": iso(a.starts_at), "ends_at": iso(a.ends_at), "shift_label": a.shift_label} for a in rows]


# ------------------------------------------------------------------ configuration (admin)
@router.get("/config/settings")
def get_settings_(db: Session = Depends(get_db), user: User = Depends(supervisor)):
    return all_settings(db)


@router.put("/config/settings")
def put_settings(body: dict[str, Any], db: Session = Depends(get_db), user: User = Depends(admin)):
    for k, v in body.items():
        if k not in DEFAULTS:
            raise HTTPException(400, f"unknown setting {k}")
        set_setting(db, k, v)
    db.commit()
    return all_settings(db)


def tariff_json(t: Tariff) -> dict:
    return {c: (iso(getattr(t, c)) if c in ("effective_from", "created_at") else getattr(t, c))
            for c in ("id", "vehicle_class", "version", "effective_from", "first_slab_minutes", "first_slab_paise",
                      "per_hour_paise", "grace_minutes", "block_minutes", "block_cap_paise", "daily_cap_paise",
                      "overnight_paise", "overnight_cutoff_hour", "free_minutes", "notes", "created_by", "created_at")}


@router.get("/config/tariffs")
def tariffs(db: Session = Depends(get_db), user: User = Depends(supervisor)):
    return [tariff_json(t) for t in db.scalars(select(Tariff).order_by(Tariff.vehicle_class, Tariff.version.desc()))]


class TariffIn(BaseModel):
    vehicle_class: str
    effective_from: datetime
    first_slab_minutes: int = 120
    first_slab_paise: int
    per_hour_paise: int
    grace_minutes: int = 10
    block_minutes: int = 720
    block_cap_paise: Optional[int] = None
    daily_cap_paise: Optional[int] = None
    overnight_paise: int = 0
    overnight_cutoff_hour: int = 0
    free_minutes: int = 0
    notes: Optional[str] = None


@router.post("/config/tariffs")
def tariff_create(body: TariffIn, db: Session = Depends(get_db), user: User = Depends(admin)):
    """Tariffs are versioned: a change is a new version effective from a date; past charges never change."""
    if body.effective_from < utcnow() - timedelta(minutes=5):
        raise HTTPException(400, "effective_from cannot be in the past")
    ver = (db.scalar(select(func.max(Tariff.version)).where(Tariff.vehicle_class == body.vehicle_class)) or 0) + 1
    t = Tariff(**body.model_dump(), version=ver, created_by=user.id)
    db.add(t)
    db.commit()
    return tariff_json(t)


class PreviewIn(TariffIn):
    entry: datetime
    exit: datetime


@router.post("/config/tariffs/preview")
def tariff_preview(body: PreviewIn, user: User = Depends(supervisor)):
    from ..domain.lookup import site_tz
    from ..domain.tariff import TariffSpec, calculate_charge

    spec = TariffSpec(**body.model_dump(exclude={"entry", "exit", "notes"}))
    return {"charge_paise": calculate_charge(body.vehicle_class, body.entry, body.exit, spec, site_tz())}


class PassTypeIn(BaseModel):
    vehicle_class: str
    name: str
    period_unit: str = "MONTH"
    period_value: int = 1
    price_paise: int
    active: bool = True
    is_default: bool = False


def pass_type_json(p: PassType) -> dict:
    return {"id": p.id, "vehicle_class": p.vehicle_class, "name": p.name, "period_unit": p.period_unit,
            "period_value": p.period_value, "price_paise": p.price_paise, "active": p.active, "is_default": p.is_default}


@router.get("/config/pass-types")
def pass_types(db: Session = Depends(get_db), user: User = Depends(supervisor)):
    return [pass_type_json(p) for p in db.scalars(select(PassType).order_by(PassType.vehicle_class, PassType.id))]


@router.post("/config/pass-types")
def pass_type_create(body: PassTypeIn, db: Session = Depends(get_db), user: User = Depends(admin)):
    p = PassType(**body.model_dump())
    db.add(p)
    db.commit()
    return pass_type_json(p)


@router.put("/config/pass-types/{pt_id}")
def pass_type_update(pt_id: int, body: PassTypeIn, db: Session = Depends(get_db), user: User = Depends(admin)):
    p = db.get(PassType, pt_id)
    for k, v in body.model_dump().items():
        setattr(p, k, v)
    db.commit()
    return pass_type_json(p)


@router.get("/config/vehicle-classes")
def vclasses(db: Session = Depends(get_db), user: User = Depends(supervisor)):
    return [{"code": v.code, "name": v.name, "enabled": v.enabled} for v in db.scalars(select(VehicleClass))]


class VClassIn(BaseModel):
    name: Optional[str] = None
    enabled: Optional[bool] = None


@router.put("/config/vehicle-classes/{code}")
def vclass_update(code: str, body: VClassIn, db: Session = Depends(get_db), user: User = Depends(admin)):
    v = db.get(VehicleClass, code)
    if v is None:
        raise HTTPException(404, "unknown class")
    if body.name is not None:
        v.name = body.name
    if body.enabled is not None:
        v.enabled = body.enabled
    db.commit()
    return {"code": v.code, "name": v.name, "enabled": v.enabled}


class GateIn(BaseModel):
    id: str
    name: str
    direction: str = "BOTH"
    schedule: list[dict] = []
    enabled: bool = True


class CameraIn(BaseModel):
    id: str
    gate_id: str
    role: str = "ANPR"
    side: str = "LEFT"
    rtsp_url: str = ""
    roi: list = []
    capture_line: list = []
    in_vector: list = [0, 1]
    enabled: bool = True


@router.get("/config/gates")
def gates(db: Session = Depends(get_db), user: User = Depends(supervisor)):
    return [{"id": g.id, "name": g.name, "direction": g.direction, "schedule": g.schedule, "enabled": g.enabled,
             "cameras": [CameraIn.model_validate(c, from_attributes=True).model_dump() for c in g.cameras]}
            for g in db.scalars(select(Gate).order_by(Gate.id))]


@router.put("/config/gates")
def gate_upsert(body: GateIn, db: Session = Depends(get_db), user: User = Depends(admin)):
    if body.direction not in ("IN", "OUT", "BOTH") or any(r.get("direction") not in ("IN", "OUT", "BOTH") for r in body.schedule):
        raise HTTPException(400, "direction must be IN, OUT or BOTH")
    g = db.get(Gate, body.id) or Gate(id=body.id)
    for k, v in body.model_dump().items():
        setattr(g, k, v)
    db.add(g)
    db.commit()
    return {"id": g.id}


@router.put("/config/cameras")
def camera_upsert(body: CameraIn, db: Session = Depends(get_db), user: User = Depends(admin)):
    c = db.get(Camera, body.id) or Camera(id=body.id)
    for k, v in body.model_dump().items():
        setattr(c, k, v)
    db.add(c)
    db.commit()
    return {"id": c.id}


# ------------------------------------------------------------------ users
class UserIn(BaseModel):
    username: str
    name: str
    role: str
    pin: Optional[str] = None
    password: Optional[str] = None
    phone: Optional[str] = None
    active: bool = True


@router.get("/users")
def users(db: Session = Depends(get_db), user: User = Depends(supervisor)):
    from .core import user_json

    return [user_json(u) for u in db.scalars(select(User).order_by(User.role, User.name))]


@router.post("/users")
def user_create(body: UserIn, db: Session = Depends(get_db), user: User = Depends(admin)):
    if body.role not in Role.ALL:
        raise HTTPException(400, "bad role")
    if body.pin and (len(body.pin) < 4 or not body.pin.isdigit()):
        raise HTTPException(400, "PIN must be at least 4 digits")
    u = User(username=body.username, name=body.name, role=body.role, phone=body.phone, active=body.active,
             pin_hash=hash_secret(body.pin) if body.pin else None,
             password_hash=hash_secret(body.password) if body.password else None)
    db.add(u)
    db.commit()
    return {"id": u.id}


@router.put("/users/{user_id}")
def user_update(user_id: int, body: UserIn, db: Session = Depends(get_db), user: User = Depends(admin)):
    u = db.get(User, user_id)
    u.name, u.role, u.phone, u.active = body.name, body.role, body.phone, body.active
    if body.pin:
        u.pin_hash = hash_secret(body.pin)
    if body.password:
        u.password_hash = hash_secret(body.password)
    db.commit()
    return {"id": u.id}


@router.post("/users/{user_id}/reset-device")
def user_reset_device(user_id: int, db: Session = Depends(get_db), user: User = Depends(admin)):
    u = db.get(User, user_id)
    u.device_id = None
    db.commit()
    return {"id": u.id, "device_bound": False}


# ------------------------------------------------------------------ audit (admin only)
@router.get("/audit")
def audit(table: Optional[str] = None, row_id: Optional[str] = None, limit: int = 200, db: Session = Depends(get_db),
          user: User = Depends(admin)):
    stmt = select(AuditLog)
    if table:
        stmt = stmt.where(AuditLog.table_name == table)
    if row_id:
        stmt = stmt.where(AuditLog.row_id == row_id)
    return [{"id": a.id, "table": a.table_name, "row_id": a.row_id, "action": a.action, "user_id": a.user_id,
             "before": a.before, "after": a.after, "created_at": iso(a.created_at)}
            for a in db.scalars(stmt.order_by(AuditLog.id.desc()).limit(min(limit, 2000)))]


# ------------------------------------------------------------------ privacy (DPDP Act)
@router.get("/privacy/vehicles/{vehicle_id}/export")
def privacy_export(vehicle_id: int, db: Session = Depends(get_db), user: User = Depends(admin)):
    return privacy.export_vehicle(db, vehicle_id)


class EraseIn(BaseModel):
    reason: str


@router.post("/privacy/vehicles/{vehicle_id}/erase")
def privacy_erase(vehicle_id: int, body: EraseIn, db: Session = Depends(get_db), user: User = Depends(admin)):
    res = privacy.erase_vehicle(db, vehicle_id, reason=body.reason)
    db.commit()
    return res
