"""JSON serialisers shared by routers."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import AnprEvent, LedgerEntry, ParkingSession, Pass, Payment, PayStatus, Vehicle
from ..domain.lookup import active_pass
from ..domain.sessions import image_url, open_session_for, pending_claims_paise
from ..db import utcnow


def iso(dt) -> Optional[str]:
    return dt.isoformat() if dt else None


def event_json(ev: AnprEvent) -> dict:
    imgs = ev.images or {}
    return {"id": ev.id, "gate_id": ev.gate_id, "camera_ids": ev.camera_ids, "direction": ev.direction,
            "wrong_way": ev.wrong_way, "vehicle_class": ev.vehicle_class, "ts": iso(ev.ts), "raw_plate": ev.raw_plate,
            "plate": ev.plate_norm, "confidence": ev.confidence, "candidates": ev.candidates, "status": ev.status,
            "review_reason": ev.review_reason, "vehicle_id": ev.vehicle_id, "session_id": ev.session_id,
            "match_type": ev.match_type, "match_distance": ev.match_distance, "matched_plate": ev.matched_plate,
            "latency_ms": ev.latency_ms,
            "images": {k: image_url(v) for k, v in imgs.items() if isinstance(v, str)},
            "extra_images": [image_url(x) for x in imgs.get("extra", []) or []]}


def session_json(db: Session, s: ParkingSession, with_images: bool = True) -> dict:
    v = db.get(Vehicle, s.vehicle_id) if s.vehicle_id else None
    d = {"id": s.id, "vehicle_id": s.vehicle_id, "plate": v.plate if v else None,
         "display_plate": v.display_plate if v else None, "vehicle_class": s.vehicle_class, "status": s.status,
         "entry_at": iso(s.entry_at), "exit_at": iso(s.exit_at), "entry_gate": s.entry_gate, "exit_gate": s.exit_gate,
         "est_duration_minutes": s.est_duration_minutes, "charge_paise": s.charge_paise, "zone_id": s.zone_id,
         "parked_location": s.parked_location, "pass_id": s.pass_id, "entry_match": s.entry_match,
         "exit_match": s.exit_match, "unpaid_flagged": s.unpaid_flagged, "note": s.note,
         "entry_event_id": s.entry_event_id, "exit_event_id": s.exit_event_id}
    if with_images:
        for key, eid in (("entry_images", s.entry_event_id), ("exit_images", s.exit_event_id)):
            ev = db.get(AnprEvent, eid) if eid else None
            d[key] = {k: image_url(val) for k, val in (ev.images or {}).items() if isinstance(val, str)} if ev else {}
    return d


def vehicle_json(db: Session, v: Vehicle, *, detail: bool = False) -> dict:
    now = utcnow()
    p = active_pass(db, v.id, now)
    open_s = open_session_for(db, v.id)
    d = {"id": v.id, "plate": v.plate, "display_plate": v.display_plate, "vehicle_class": v.vehicle_class,
         "first_seen": iso(v.first_seen), "last_seen": iso(v.last_seen), "phone": v.phone, "name": v.name,
         "notes": v.notes, "balance_paise": v.balance_paise, "pending_claims_paise": pending_claims_paise(db, v.id),
         "pass": {"id": p.id, "ends_at": iso(p.ends_at), "starts_at": iso(p.starts_at)} if p else None,
         "open_session": session_json(db, open_s) if open_s else None}
    if detail:
        from ..domain.passes import is_pass_candidate, pass_dict

        d["pass_candidate"] = p is None and is_pass_candidate(db, v.id)
        d["sessions"] = [session_json(db, s, with_images=False) for s in db.scalars(
            select(ParkingSession).where(ParkingSession.vehicle_id == v.id).order_by(ParkingSession.id.desc()).limit(50))]
        from ..domain.payments import payment_dict

        d["payments"] = [payment_dict(db, pm) for pm in db.scalars(
            select(Payment).where(Payment.vehicle_id == v.id).order_by(Payment.id.desc()).limit(50))]
        d["ledger"] = [{"id": e.id, "kind": e.kind, "amount_paise": e.amount_paise, "session_id": e.session_id,
                        "payment_id": e.payment_id, "pass_id": e.pass_id, "reason": e.reason, "created_at": iso(e.created_at)}
                       for e in db.scalars(select(LedgerEntry).where(LedgerEntry.vehicle_id == v.id)
                                           .order_by(LedgerEntry.id.desc()).limit(100))]
        d["passes"] = [pass_dict(db, x) for x in db.scalars(select(Pass).where(Pass.vehicle_id == v.id)
                                                             .order_by(Pass.id.desc()))]
        d["ledger_balance_paise"] = int(db.scalar(select(func.coalesce(func.sum(LedgerEntry.amount_paise), 0))
                                                  .where(LedgerEntry.vehicle_id == v.id)) or 0)
    return d


def session_paid(db: Session, sid: int) -> int:
    return int(db.scalar(select(func.coalesce(func.sum(Payment.amount_paise), 0)).where(
        Payment.session_id == sid, Payment.status.in_([PayStatus.CONFIRMED, PayStatus.CLAIMED_OFFLINE]))) or 0)
