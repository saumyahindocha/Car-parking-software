"""Worker / guard app endpoints: to-collect list, search, quotes, UPI & cash, receipts, shifts,
cash in hand, handovers, passes, disputes, alerts, offline sync."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..adapters.gateway import GatewayUnavailable
from ..db import utcnow
from ..domain import cash, disputes, payments, plates
from ..domain import receipts as rec_svc
from ..domain import sessions as sess_svc
from ..domain.passes import pass_dict
from ..domain.settings import get_setting
from ..models import (Alert, AnprEvent, ParkingSession, Payment, PlateCorrection, Receipt, Role, SessionStatus, User,
                      Vehicle)
from .deps import collector, current_user, get_db, require, staff
from .serial import iso, session_json, vehicle_json

router = APIRouter(prefix="/api")
guard_or_up = require(Role.GUARD, Role.WORKER, Role.SUPERVISOR, Role.ADMIN)


# ------------------------------------------------------------------ to collect
@router.get("/collect/list")
def to_collect(hours: Optional[int] = None, zone_id: Optional[int] = None, db: Session = Depends(get_db),
               user: User = Depends(collector)):
    h = hours or int(get_setting(db, "to_collect_hours"))
    since = utcnow() - timedelta(hours=h)
    stmt = (select(ParkingSession, Vehicle, AnprEvent)
            .join(Vehicle, ParkingSession.vehicle_id == Vehicle.id)
            .join(AnprEvent, AnprEvent.id == ParkingSession.entry_event_id, isouter=True)
            .where(ParkingSession.status == SessionStatus.OPEN, ParkingSession.entry_at >= since))
    if zone_id:
        stmt = stmt.where(ParkingSession.zone_id == zone_id)
    rows = db.execute(stmt.order_by(ParkingSession.entry_at.desc()).limit(500)).all()
    n_visits = int(get_setting(db, "pass_candidate_visits"))
    vids = [v.id for _, v, _ in rows]
    visits = dict(db.execute(select(ParkingSession.vehicle_id, func.count(ParkingSession.id)).where(
        ParkingSession.vehicle_id.in_(vids), ParkingSession.entry_at >= utcnow() - timedelta(days=30))
        .group_by(ParkingSession.vehicle_id)).all()) if vids else {}
    return [{"session_id": s.id, "vehicle_id": v.id, "plate": v.plate, "display_plate": v.display_plate,
             "vehicle_class": s.vehicle_class, "entry_at": iso(s.entry_at), "gate_id": s.entry_gate, "zone_id": s.zone_id,
             "previous_due_paise": max(0, v.balance_paise), "credit_paise": max(0, -v.balance_paise),
             "plate_image": sess_svc.image_url((ev.images or {}).get("plate_crop")) if ev else None,
             "entry_match": s.entry_match, "unpaid_flagged": s.unpaid_flagged, "phone_known": bool(v.phone),
             "pass_candidate": visits.get(v.id, 0) >= n_visits} for s, v, ev in rows]


# ------------------------------------------------------------------ vehicles & sessions
@router.get("/vehicles/search")
def search(q: str, db: Session = Depends(get_db), user: User = Depends(staff)):
    return [{**vehicle_json(db, r["vehicle"]), "distance": r["distance"], "exact": r.get("exact", r["distance"] == 0)}
            for r in sess_svc.search_plate(db, q)]


@router.get("/vehicles/{vehicle_id}")
def vehicle_detail(vehicle_id: int, db: Session = Depends(get_db), user: User = Depends(staff)):
    v = db.get(Vehicle, vehicle_id)
    if v is None:
        raise HTTPException(404, "vehicle not found")
    return vehicle_json(db, v, detail=True)


class PhoneIn(BaseModel):
    phone: str
    name: Optional[str] = None


@router.post("/vehicles/{vehicle_id}/contact")
def set_contact(vehicle_id: int, body: PhoneIn, db: Session = Depends(get_db), user: User = Depends(collector)):
    v = db.get(Vehicle, vehicle_id)
    digits = "".join(c for c in body.phone if c.isdigit())[-10:]
    if len(digits) != 10:
        raise HTTPException(400, "enter a 10-digit mobile number")
    v.phone = digits
    if body.name:
        v.name = body.name
    db.commit()
    return vehicle_json(db, v)


@router.get("/sessions/{session_id}")
def session_detail(session_id: int, db: Session = Depends(get_db), user: User = Depends(staff)):
    s = db.get(ParkingSession, session_id)
    if s is None:
        raise HTTPException(404, "session not found")
    return session_json(db, s)


class CorrectIn(BaseModel):
    plate: str


@router.post("/sessions/{session_id}/correct-plate")
def correct_plate(session_id: int, body: CorrectIn, db: Session = Depends(get_db), user: User = Depends(collector)):
    """Worker says the ANPR read is wrong. Moves an unpaid session to the right vehicle; logged for review."""
    s = sess_svc.correct_session_plate(db, session_id, body.plate, user_id=user.id)
    db.commit()
    return session_json(db, s)


@router.get("/sessions/{session_id}/quote")
def quote(session_id: int, duration_minutes: int = Query(..., gt=0), db: Session = Depends(get_db),
          user: User = Depends(collector)):
    return payments.quote_session(db, session_id, duration_minutes).as_dict()


# ------------------------------------------------------------------ payments
class PayIn(BaseModel):
    purpose: str = "SESSION"  # SESSION | DUES | PASS
    session_id: Optional[int] = None
    vehicle_id: Optional[int] = None
    duration_minutes: Optional[int] = None
    pass_type_id: Optional[int] = None
    phone: Optional[str] = None
    override_paise: Optional[int] = None
    override_reason: Optional[str] = None
    supervisor_pin: Optional[str] = None
    client_uuid: Optional[str] = None
    parked_location: Optional[str] = None
    expected_amount_paise: Optional[int] = None
    receipt_code: Optional[str] = None  # phone-generated code (cash), so a QR shown before a timeout stays valid


def _quote_for(db: Session, body: PayIn, user: User) -> tuple[payments.Quote, str, Optional[int]]:
    if body.purpose == "SESSION":
        if not body.session_id or not body.duration_minutes:
            raise HTTPException(400, "session_id and duration_minutes required")
        return payments.quote_session(db, body.session_id, body.duration_minutes), "SESSION", None
    if body.purpose == "DUES":
        return payments.quote_dues(db, body.vehicle_id), "DUES", None
    if body.purpose == "PASS":
        vid = body.vehicle_id or (db.get(ParkingSession, body.session_id).vehicle_id if body.session_id else None)
        if not vid or not body.pass_type_id:
            raise HTTPException(400, "vehicle_id and pass_type_id required")
        q = payments.quote_pass(db, vid, body.pass_type_id)
        p = payments.create_pending_pass(db, vid, body.pass_type_id, user_id=user.id, channel="WORKER")
        return q, "PASS", p.id
    raise HTTPException(400, "unknown purpose")


def _phone(p: Optional[str]) -> Optional[str]:
    if not p:
        return None
    d = "".join(c for c in p if c.isdigit())[-10:]
    if len(d) != 10:
        raise HTTPException(400, "enter a 10-digit mobile number")
    return d


@router.post("/payments/upi")
def pay_upi(body: PayIn, db: Session = Depends(get_db), user: User = Depends(collector)):
    q, purpose, pass_id = _quote_for(db, body, user)
    if body.expected_amount_paise is not None and body.override_paise is None and body.expected_amount_paise != q.amount_paise:
        raise HTTPException(409, f"amount changed to ₹{q.amount_paise / 100:.0f}; refresh")
    p = payments.start_upi(db, q, user=user, purpose=purpose, pass_id=pass_id, phone=_phone(body.phone),
                           override_paise=body.override_paise, override_reason=body.override_reason,
                           supervisor_pin=body.supervisor_pin, parked_location=body.parked_location,
                           client_uuid=body.client_uuid)
    db.commit()
    return payments.payment_dict(db, p)


@router.post("/payments/cash")
def pay_cash(body: PayIn, db: Session = Depends(get_db), user: User = Depends(collector)):
    q, purpose, pass_id = _quote_for(db, body, user)
    if body.expected_amount_paise is not None and body.override_paise is None and body.expected_amount_paise != q.amount_paise:
        raise HTTPException(409, f"amount changed to ₹{q.amount_paise / 100:.0f}; refresh")
    p = payments.record_cash(db, q, user=user, purpose=purpose, pass_id=pass_id, phone=_phone(body.phone),
                             override_paise=body.override_paise, override_reason=body.override_reason,
                             supervisor_pin=body.supervisor_pin, client_uuid=body.client_uuid,
                             parked_location=body.parked_location, receipt_code=body.receipt_code)
    db.commit()
    return {**payments.payment_dict(db, p), "cash": cash.holding_dict(db, user.id)}


@router.get("/payments/{payment_id}")
def payment_status(payment_id: int, db: Session = Depends(get_db), user: User = Depends(staff)):
    p = db.get(Payment, payment_id)
    if p is None:
        raise HTTPException(404, "payment not found")
    try:
        payments.check_upi_status(db, p)
        db.commit()
    except GatewayUnavailable:
        db.rollback()
    return payments.payment_dict(db, p)


@router.post("/payments/{payment_id}/claim-offline")
def claim(payment_id: int, db: Session = Depends(get_db), user: User = Depends(collector)):
    p = db.get(Payment, payment_id)
    if p is None or p.mode != "UPI":
        raise HTTPException(404, "UPI payment not found")
    payments.claim_offline(db, p, user)
    db.commit()
    return payments.payment_dict(db, p)


class CancelIn(BaseModel):
    reason: Optional[str] = None


@router.post("/payments/{payment_id}/cancel")
def cancel(payment_id: int, body: CancelIn, db: Session = Depends(get_db), user: User = Depends(collector)):
    """Abandon an unpaid UPI QR (customer switched to cash or left)."""
    p = db.get(Payment, payment_id)
    if p is None or p.mode != "UPI":
        raise HTTPException(404, "UPI payment not found")
    payments.cancel_payment(db, p, user, body.reason or "cancelled by worker")
    db.commit()
    return payments.payment_dict(db, p)


# ------------------------------------------------------------------ receipts
class SendIn(BaseModel):
    phone: str


@router.post("/receipts/{code}/shown")
def receipt_shown(code: str, db: Session = Depends(get_db), user: User = Depends(collector)):
    r = db.scalars(select(Receipt).where(Receipt.code == code)).first()
    if r is None:
        raise HTTPException(404, "receipt not found")
    rec_svc.mark_shown(db, r)
    db.commit()
    return {"code": r.code, "delivery_status": r.delivery_status, "channel": r.channel}


@router.post("/receipts/{code}/send")
def receipt_send(code: str, body: SendIn, db: Session = Depends(get_db), user: User = Depends(collector)):
    r = db.scalars(select(Receipt).where(Receipt.code == code)).first()
    if r is None:
        raise HTTPException(404, "receipt not found")
    rec_svc.send_to_phone(db, r, _phone(body.phone))
    db.commit()
    return {"code": r.code, "delivery_status": r.delivery_status, "channel": r.channel}


# ------------------------------------------------------------------ shifts & cash
class ShiftOpenIn(BaseModel):
    zone_id: Optional[int] = None


class ShiftCloseIn(BaseModel):
    note: Optional[str] = None


@router.post("/shifts/open")
def shift_open(body: ShiftOpenIn, db: Session = Depends(get_db), user: User = Depends(collector)):
    s = cash.open_shift(db, user, zone_id=body.zone_id)
    db.commit()
    return {"id": s.id, "zone_id": s.zone_id, "opened_at": iso(s.opened_at), **cash.shift_totals(db, s.id)}


@router.get("/shifts/current")
def shift_current(db: Session = Depends(get_db), user: User = Depends(collector)):
    s = cash.current_shift(db, user.id)
    if s is None:
        return None
    return {"id": s.id, "zone_id": s.zone_id, "opened_at": iso(s.opened_at), **cash.shift_totals(db, s.id),
            "cash": cash.holding_dict(db, user.id)}


@router.post("/shifts/close")
def shift_close(body: ShiftCloseIn, db: Session = Depends(get_db), user: User = Depends(collector)):
    s = cash.close_shift(db, user, note=body.note)
    db.commit()
    return {"id": s.id, "closed_at": iso(s.closed_at), "upi_total_paise": s.upi_total_paise,
            "cash_total_paise": s.cash_total_paise, "handed_over_paise": s.handed_over_paise,
            "variance_paise": s.variance_paise}


@router.get("/me/cash")
def my_cash(db: Session = Depends(get_db), user: User = Depends(collector)):
    return cash.holding_dict(db, user.id)


class HandoverIn(BaseModel):
    amount_paise: int
    denominations: dict[str, int]
    to_user: Optional[int] = None
    client_uuid: Optional[str] = None


@router.post("/cash/handovers")
def handover_declare(body: HandoverIn, db: Session = Depends(get_db), user: User = Depends(collector)):
    h = cash.declare_handover(db, user, body.amount_paise, body.denominations, to_user=body.to_user,
                              client_uuid=body.client_uuid)
    db.commit()
    return cash.handover_dict(db, h)


@router.get("/cash/handovers/mine")
def my_handovers(db: Session = Depends(get_db), user: User = Depends(collector)):
    from ..models import CashHandover

    return [cash.handover_dict(db, h) for h in db.scalars(select(CashHandover).where(CashHandover.from_user == user.id)
                                                          .order_by(CashHandover.id.desc()).limit(20))]


# ------------------------------------------------------------------ passes
class PassSellIn(BaseModel):
    vehicle_id: Optional[int] = None
    plate: Optional[str] = None
    pass_type_id: int
    mode: str = "UPI"
    phone: Optional[str] = None
    client_uuid: Optional[str] = None
    expected_amount_paise: Optional[int] = None
    receipt_code: Optional[str] = None


@router.post("/passes/sell")
def pass_sell(body: PassSellIn, db: Session = Depends(get_db), user: User = Depends(collector)):
    vid = body.vehicle_id
    if vid is None:
        norm = plates.correct(plates.normalise(body.plate or ""), get_setting(db, "state_codes"))
        if not norm.valid:
            raise HTTPException(400, "enter a valid plate")
        from ..models import PassType

        pt = db.get(PassType, body.pass_type_id)
        vid = sess_svc.get_or_create_vehicle(db, norm.plate, pt.vehicle_class, utcnow()).id
    pay = PayIn(purpose="PASS", vehicle_id=vid, pass_type_id=body.pass_type_id, phone=body.phone, client_uuid=body.client_uuid,
                expected_amount_paise=body.expected_amount_paise, receipt_code=body.receipt_code)
    return pay_cash(pay, db, user) if body.mode == "CASH" else pay_upi(pay, db, user)


@router.get("/passes/vehicle/{vehicle_id}")
def vehicle_passes(vehicle_id: int, db: Session = Depends(get_db), user: User = Depends(staff)):
    from ..models import Pass

    return [pass_dict(db, p) for p in db.scalars(select(Pass).where(Pass.vehicle_id == vehicle_id).order_by(Pass.id.desc()))]


# ------------------------------------------------------------------ disputes & alerts (guard mode)
class DisputeIn(BaseModel):
    vehicle_id: Optional[int] = None
    plate: Optional[str] = None
    session_id: Optional[int] = None
    claimed_paise: Optional[int] = None
    claimed_mode: str = "CASH"
    claimed_when: Optional[str] = None
    note: Optional[str] = None
    alert_id: Optional[int] = None
    client_uuid: Optional[str] = None


@router.post("/disputes")
def dispute_create(body: DisputeIn, db: Session = Depends(get_db), user: User = Depends(staff)):
    vid = body.vehicle_id
    if vid is None and body.alert_id:
        a = db.get(Alert, body.alert_id)
        vid = a.vehicle_id if a else None
        body.session_id = body.session_id or (a.session_id if a else None)
    if vid is None and body.plate:
        v = sess_svc.find_vehicle(db, plates.normalise(body.plate))
        vid = v.id if v else None
    if vid is None:
        raise HTTPException(400, "vehicle not found")
    d = disputes.raise_dispute(db, vehicle_id=vid, session_id=body.session_id, raised_by_role=user.role,
                               raised_by_user=user.id, claimed_paise=body.claimed_paise, claimed_mode=body.claimed_mode,
                               claimed_when=body.claimed_when, note=body.note, alert_id=body.alert_id,
                               client_uuid=body.client_uuid)
    db.commit()
    return disputes.dispute_dict(db, d)


def alert_json(a: Alert) -> dict:
    return {"id": a.id, "kind": a.kind, "severity": a.severity, "gate_id": a.gate_id, "session_id": a.session_id,
            "vehicle_id": a.vehicle_id, "event_id": a.event_id, "message": a.message, "data": a.data,
            "created_at": iso(a.created_at), "acknowledged_by": a.acknowledged_by, "acknowledged_at": iso(a.acknowledged_at),
            "note": a.note}


@router.get("/alerts")
def alerts(kind: Optional[str] = None, open_only: bool = True, gate_id: Optional[str] = None, hours: int = 24,
           db: Session = Depends(get_db), user: User = Depends(guard_or_up)):
    stmt = select(Alert).where(Alert.created_at >= utcnow() - timedelta(hours=hours))
    if kind:
        stmt = stmt.where(Alert.kind == kind)
    if open_only:
        stmt = stmt.where(Alert.acknowledged_at.is_(None))
    if gate_id:
        stmt = stmt.where(Alert.gate_id == gate_id)
    return [alert_json(a) for a in db.scalars(stmt.order_by(Alert.id.desc()).limit(200))]


class AckIn(BaseModel):
    note: Optional[str] = None


@router.post("/alerts/{alert_id}/ack")
def alert_ack(alert_id: int, body: AckIn, db: Session = Depends(get_db), user: User = Depends(guard_or_up)):
    a = db.get(Alert, alert_id)
    if a is None:
        raise HTTPException(404, "alert not found")
    a.acknowledged_by, a.acknowledged_at, a.note = user.id, utcnow(), body.note
    db.commit()
    return alert_json(a)


# ------------------------------------------------------------------ offline sync (idempotent batch)
class SyncItem(BaseModel):
    # CASH | UPI_CLAIM | DISPUTE | HANDOVER | RECEIPT_SHOWN | CONTACT | ALERT_ACK | PLATE_CORRECTION
    # | SHIFT_OPEN | SHIFT_CLOSE
    type: str
    client_uuid: str
    created_at: datetime
    data: dict[str, Any] = Field(default_factory=dict)


class SyncIn(BaseModel):
    items: list[SyncItem]


def _record_sync_failure(db: Session, user: User, it: SyncItem, err: str) -> None:
    """Failed offline items become a supervisor alert (once per client_uuid) so they are seen centrally."""
    seen = db.scalars(select(Alert).where(Alert.kind == "SYNC_FAILED", Alert.event_id == it.client_uuid)).first()
    if seen is None:
        db.add(Alert(kind="SYNC_FAILED", severity="WARN", event_id=it.client_uuid,
                     message=f"{user.name}: offline {it.type} could not be applied: {err}",
                     data={"user_id": user.id, "type": it.type, "created_at": it.created_at.isoformat(),
                           "data": {k: v for k, v in it.data.items() if k != "phone"}, "error": err}))
        db.commit()


@router.post("/sync")
def offline_sync(body: SyncIn, db: Session = Depends(get_db), user: User = Depends(staff)):
    """Apply actions queued on the phone while offline, in order. Each item is idempotent on client_uuid."""
    results = []
    for it in body.items:
        d = it.data
        try:
            if it.type == "CASH":
                sess_id = d.get("session_id")
                pass_type_id = d.get("pass_type_id")
                q, purpose, pass_id = payments._offline_quote(
                    db, sess_id, d.get("vehicle_id"), d.get("duration_minutes"), int(d.get("dues_paise", 0)),
                    int(d["amount_paise"]), pass_type_id, user, it.created_at)
                p = payments.record_cash(db, q, user=user, purpose=purpose, pass_id=pass_id, phone=_phone(d.get("phone")),
                                         client_uuid=it.client_uuid, client_created_at=it.created_at, offline_sync=True,
                                         receipt_code=d.get("receipt_code"))
                results.append({"client_uuid": it.client_uuid, "ok": True, "payment": payments.payment_dict(db, p)})
            elif it.type == "UPI_CLAIM":
                p = payments.record_offline_claim(
                    db, user=user, client_uuid=it.client_uuid, amount_paise=int(d["amount_paise"]), txn_ref=d["txn_ref"],
                    client_created_at=it.created_at, session_id=d.get("session_id"), vehicle_id=d.get("vehicle_id"),
                    duration_minutes=d.get("duration_minutes"), dues_paise=int(d.get("dues_paise", 0)),
                    phone=_phone(d.get("phone")), pass_type_id=d.get("pass_type_id"))
                results.append({"client_uuid": it.client_uuid, "ok": True, "payment": payments.payment_dict(db, p)})
            elif it.type == "DISPUTE":
                dd = disputes.raise_dispute(db, vehicle_id=d["vehicle_id"], session_id=d.get("session_id"),
                                            raised_by_role=user.role, raised_by_user=user.id,
                                            claimed_paise=d.get("claimed_paise"), claimed_mode=d.get("claimed_mode", "CASH"),
                                            note=d.get("note"), alert_id=d.get("alert_id"), client_uuid=it.client_uuid)
                results.append({"client_uuid": it.client_uuid, "ok": True, "dispute_id": dd.id})
            elif it.type == "HANDOVER":
                h = cash.declare_handover(db, user, int(d["amount_paise"]), d["denominations"], client_uuid=it.client_uuid)
                results.append({"client_uuid": it.client_uuid, "ok": True, "handover_id": h.id})
            elif it.type == "RECEIPT_SHOWN":
                p = db.scalars(select(Payment).where(Payment.client_uuid == d["payment_client_uuid"])).first()
                if p and p.receipt_id:
                    rec_svc.mark_shown(db, db.get(Receipt, p.receipt_id))
                results.append({"client_uuid": it.client_uuid, "ok": True})
            elif it.type == "CONTACT":
                v = db.get(Vehicle, d["vehicle_id"])
                v.phone = _phone(d["phone"])
                results.append({"client_uuid": it.client_uuid, "ok": True})
            elif it.type == "ALERT_ACK":
                a = db.get(Alert, d["alert_id"])
                if a is not None and a.acknowledged_at is None:
                    a.acknowledged_by, a.acknowledged_at, a.note = user.id, it.created_at, d.get("note")
                results.append({"client_uuid": it.client_uuid, "ok": True})
            elif it.type == "PLATE_CORRECTION":
                sess_svc.correct_session_plate(db, d["session_id"], d["plate"], user_id=user.id)
                results.append({"client_uuid": it.client_uuid, "ok": True})
            elif it.type == "SHIFT_OPEN":
                sh = cash.current_shift(db, user.id) or cash.open_shift(db, user, zone_id=d.get("zone_id"), at=it.created_at)
                results.append({"client_uuid": it.client_uuid, "ok": True, "shift_id": sh.id})
            elif it.type == "SHIFT_CLOSE":
                sh = cash.close_shift(db, user, note=d.get("note"), at=it.created_at)
                results.append({"client_uuid": it.client_uuid, "ok": True, "shift_id": sh.id})
            else:
                results.append({"client_uuid": it.client_uuid, "ok": False, "error": f"unknown type {it.type}"})
            db.commit()
        except (ValueError, LookupError, PermissionError, KeyError, HTTPException) as e:
            db.rollback()
            err = str(getattr(e, "detail", e))
            results.append({"client_uuid": it.client_uuid, "ok": False, "error": err})
            _record_sync_failure(db, user, it, err)
    return {"results": results, "cash": cash.holding_dict(db, user.id) if user.role != Role.GUARD else None}
