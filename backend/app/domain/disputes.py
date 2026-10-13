"""Payment disputes ("I paid!"): auto-linked to the worker on duty in the vehicle's zone."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import events
from ..db import utcnow
from ..models import Payment, PaymentDispute, ParkingSession, PayStatus, Role, User, Vehicle
from .lookup import worker_on_duty, zone_for_gate


class DisputeError(ValueError):
    pass


def _latest_session(db: Session, vehicle_id: int) -> Optional[ParkingSession]:
    return db.scalars(select(ParkingSession).where(ParkingSession.vehicle_id == vehicle_id)
                      .order_by(ParkingSession.id.desc())).first()


def raise_dispute(db: Session, *, vehicle_id: int, raised_by_role: str, session_id: Optional[int] = None,
                  raised_by_user: Optional[int] = None, claimed_paise: Optional[int] = None, claimed_mode: str = "CASH",
                  claimed_when: Optional[str] = None, note: Optional[str] = None, alert_id: Optional[int] = None,
                  client_uuid: Optional[str] = None) -> PaymentDispute:
    if client_uuid:
        ex = db.scalars(select(PaymentDispute).where(PaymentDispute.client_uuid == client_uuid)).first()
        if ex is not None:
            return ex
    veh = db.get(Vehicle, vehicle_id)
    if veh is None:
        raise DisputeError("vehicle not found")
    sess = db.get(ParkingSession, session_id) if session_id else _latest_session(db, vehicle_id)
    zone_id, worker_id = None, None
    if sess is not None:
        zone_id = sess.zone_id or (z.id if (z := zone_for_gate(db, sess.entry_gate)) else None)
        # a worker who recorded any payment for this session is the most direct link
        pay = db.scalars(select(Payment).where(Payment.session_id == sess.id, Payment.collected_by.is_not(None))
                         .order_by(Payment.id.desc())).first()
        if pay is not None and pay.status in (PayStatus.CONFIRMED, PayStatus.CLAIMED_OFFLINE):
            worker_id = pay.collected_by
        at = sess.entry_at or sess.exit_at
        if worker_id is None and at is not None:
            worker_id = worker_on_duty(db, zone_id, at)
    d = PaymentDispute(vehicle_id=vehicle_id, session_id=sess.id if sess else None, claimed_paise=claimed_paise,
                       claimed_mode=claimed_mode, claimed_when=claimed_when, zone_id=zone_id, worker_id=worker_id,
                       raised_by_role=raised_by_role, raised_by_user=raised_by_user, alert_id=alert_id, note=note,
                       client_uuid=client_uuid)
    db.add(d)
    db.flush()
    events.emit(db, "dispute.new", dispute_dict(db, d))
    events.emit(db, "review.new", {"dispute_id": d.id, "reason": "DISPUTE"})
    return d


def resolve_dispute(db: Session, dispute_id: int, supervisor: User, outcome: str, note: str) -> PaymentDispute:
    if supervisor.role not in (Role.SUPERVISOR, Role.ADMIN):
        raise PermissionError("supervisor only")
    if outcome not in ("UPHELD", "REJECTED", "UNRESOLVED"):
        raise DisputeError("outcome must be UPHELD, REJECTED or UNRESOLVED")
    if not note or not note.strip():
        raise DisputeError("resolution note required")
    d = db.get(PaymentDispute, dispute_id)
    if d is None or d.status != "OPEN":
        raise DisputeError("dispute not open")
    d.status, d.resolution_note, d.resolved_by, d.resolved_at = outcome, note, supervisor.id, utcnow()
    db.flush()
    return d


def dispute_dict(db: Session, d: PaymentDispute) -> dict:
    veh = db.get(Vehicle, d.vehicle_id)
    w = db.get(User, d.worker_id) if d.worker_id else None
    return {"id": d.id, "vehicle_id": d.vehicle_id, "plate": veh.plate if veh else None, "session_id": d.session_id,
            "claimed_paise": d.claimed_paise, "claimed_mode": d.claimed_mode, "claimed_when": d.claimed_when,
            "zone_id": d.zone_id, "worker_id": d.worker_id, "worker_name": w.name if w else None,
            "raised_by_role": d.raised_by_role, "status": d.status, "note": d.note,
            "resolution_note": d.resolution_note, "created_at": d.created_at.isoformat(),
            "balance_paise": veh.balance_paise if veh else None}
