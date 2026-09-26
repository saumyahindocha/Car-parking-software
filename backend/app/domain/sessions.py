"""ANPR event processing and parking-session lifecycle (spec sections 4, 10, 12)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import events
from ..db import utcnow
from ..models import (
    Alert,
    AnprEvent,
    Direction,
    EventStatus,
    Gate,
    LedgerKind,
    ParkingSession,
    Pass,
    Payment,
    PayStatus,
    PlateCorrection,
    SessionStatus,
    Tariff,
    Vehicle,
    VehicleClass,
)
from . import ledger, plates
from .lookup import active_pass, gate_direction, get_tariff, site_tz, zone_for_gate
from .settings import get_setting
from .tariff import calculate_charge

log = logging.getLogger(__name__)


# ------------------------------------------------------------------ helpers
def find_vehicle(db: Session, plate: str) -> Optional[Vehicle]:
    return db.scalars(select(Vehicle).where(Vehicle.plate == plate)).first()


def get_or_create_vehicle(db: Session, plate: str, vehicle_class: str, seen_at: datetime) -> Vehicle:
    v = find_vehicle(db, plate)
    if v is None:
        v = Vehicle(plate=plate, plate_canon=plates.canonical(plate), display_plate=plates.display(plate), vehicle_class=vehicle_class,
                    first_seen=seen_at, last_seen=seen_at, balance_paise=0)
        db.add(v)
        db.flush()
    return v


def open_session_for(db: Session, vehicle_id: int) -> Optional[ParkingSession]:
    return db.scalars(select(ParkingSession).where(
        ParkingSession.vehicle_id == vehicle_id,
        ParkingSession.status.in_(SessionStatus.ACTIVE),
    ).order_by(ParkingSession.entry_at.desc())).first()


def session_paid_paise(db: Session, session_id: int, include_claimed: bool = True) -> int:
    statuses = [PayStatus.CONFIRMED] + ([PayStatus.CLAIMED_OFFLINE] if include_claimed else [])
    return int(db.scalar(select(func.coalesce(func.sum(Payment.amount_paise), 0)).where(
        Payment.session_id == session_id, Payment.status.in_(statuses))) or 0)


def pending_claims_paise(db: Session, vehicle_id: int) -> int:
    """Offline UPI claims not yet confirmed (not in the ledger yet)."""
    return int(db.scalar(select(func.coalesce(func.sum(Payment.amount_paise), 0)).where(
        Payment.vehicle_id == vehicle_id, Payment.status == PayStatus.CLAIMED_OFFLINE)) or 0)


def image_url(rel: Optional[str]) -> Optional[str]:
    return f"/api/images/{rel}" if rel else None


@dataclass
class Candidate:
    vehicle: Vehicle
    distance: int
    session: Optional[ParkingSession] = None
    reason: str = ""


@dataclass
class ProcessResult:
    event: AnprEvent
    session: Optional[ParkingSession] = None
    exit_display: Optional[dict] = None
    alert: Optional[Alert] = None
    notes: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ approximate matching
def _regulars_filter(at: datetime):
    """Pass holders and vehicles with a non-zero balance: people we must recognise."""
    pass_vids = select(Pass.vehicle_id).where(Pass.status == "ACTIVE", Pass.ends_at > at - timedelta(days=7))
    return (Vehicle.id.in_(pass_vids)) | (Vehicle.balance_paise != 0)


def approx_open_sessions(db: Session, read: str, vehicle_class: str, before: datetime,
                         tolerance: int) -> list[Candidate]:
    rows = db.execute(select(ParkingSession.id, Vehicle.id, Vehicle.plate, ParkingSession.entry_at)
                      .join(Vehicle, ParkingSession.vehicle_id == Vehicle.id).where(
        ParkingSession.status.in_(SessionStatus.ACTIVE),
        ParkingSession.vehicle_class == vehicle_class,
        ParkingSession.entry_at <= before,
        func.length(Vehicle.plate).between(len(read) - tolerance, len(read) + tolerance),
    )).all()
    ca = plates.canonical(read)
    hits = []
    for sid, vid, plate, entry_at in rows:
        d = plates.levenshtein(ca, plates.canonical(plate), tolerance)
        if d <= tolerance:
            hits.append((d, -entry_at.timestamp(), sid, vid))
    hits.sort()
    return [Candidate(db.get(Vehicle, vid), d, db.get(ParkingSession, sid), "open_session") for d, _, sid, vid in hits]


def approx_regulars(db: Session, read: str, vehicle_class: str, at: datetime, tolerance: int,
                    exclude: set[int] | None = None, confusion_only: bool = False) -> list[Candidate]:
    ca = plates.canonical(read)
    base = select(Vehicle.id, Vehicle.plate).where(Vehicle.vehicle_class == vehicle_class, _regulars_filter(at))
    if confusion_only:
        rows = db.execute(base.where(Vehicle.plate_canon == ca)).all()
    else:
        rows = db.execute(base.where(func.length(Vehicle.plate).between(len(read) - tolerance, len(read) + tolerance))).all()
    hits = []
    for vid, plate in rows:
        if exclude and vid in exclude:
            continue
        d = 0 if confusion_only else plates.levenshtein(ca, plates.canonical(plate), tolerance)
        if d <= tolerance:
            hits.append((d, vid))
    hits.sort()
    return [Candidate(db.get(Vehicle, vid), d, None, "regular") for d, vid in hits]


def is_doubtful(db: Session, ev: AnprEvent, plate: str) -> bool:
    return (not plates.is_valid(plate, get_setting(db, "state_codes"))
            or ev.confidence < float(get_setting(db, "approx_regular_max_confidence")))


def regular_match(db: Session, ev: AnprEvent, plate: str, vclass: str, tol: int) -> Optional[Candidate]:
    """For an unknown plate, only trust a real one-character edit to a regular when the read itself
    is doubtful; confusion-only matches (B/8, 0/O...) are always accepted."""
    cands = approx_regulars(db, plate, vclass, ev.ts, tol, confusion_only=not is_doubtful(db, ev, plate))
    return unique_best(cands)


def unique_best(cands: list[Candidate]) -> Optional[Candidate]:
    """Exactly one candidate at the best distance -> strong match; otherwise ambiguous."""
    if not cands:
        return None
    best = cands[0].distance
    top = [c for c in cands if c.distance == best]
    return top[0] if len(top) == 1 else None


def search_plate(db: Session, query: str, tolerance: int | None = None, limit: int = 10,
                 vehicle_class: str | None = None) -> list[dict]:
    """Ranked plate search for worker app / dashboard (exact, then confusion + Levenshtein)."""
    q = plates.normalise(query)
    if not q:
        return []
    tol = tolerance if tolerance is not None else int(get_setting(db, "approx_tolerance"))
    stmt = select(Vehicle)
    if vehicle_class:
        stmt = stmt.where(Vehicle.vehicle_class == vehicle_class)
    if len(q) < 6:
        # partial input: substring search
        stmt = stmt.where(Vehicle.plate.contains(q)).order_by(Vehicle.last_seen.desc()).limit(limit)
        return [{"vehicle": v, "distance": 0} for v in db.scalars(stmt).all()]
    lo, hi = len(q) - tol, len(q) + tol
    cands = db.scalars(stmt.where(func.length(Vehicle.plate).between(lo, hi))).all()
    ranked = []
    cq = plates.canonical(q)
    for v in cands:
        d = 0 if v.plate == q else plates.levenshtein(cq, plates.canonical(v.plate), tol)
        if d <= tol:
            ranked.append((v.plate != q, d, v))
    ranked.sort(key=lambda x: (x[0], x[1], -x[2].last_seen.timestamp()))
    return [{"vehicle": v, "distance": d, "exact": not inexact} for inexact, d, v in ranked[:limit]]


# ------------------------------------------------------------------ event ingest
def _pick_plate(db: Session, payload: dict) -> tuple[Optional[str], bool]:
    """Best plate from the read + candidates, preferring valid Indian formats."""
    codes = get_setting(db, "state_codes")
    options = []
    if payload.get("plate"):
        options.append((payload["plate"], float(payload.get("confidence") or 0)))
    for c in payload.get("candidates") or []:
        if c.get("plate"):
            options.append((c["plate"], float(c.get("confidence") or 0)))
    best_valid = None
    for raw, conf in options:
        corr = plates.correct(raw, codes)
        if corr.valid and (best_valid is None or conf > best_valid[1]):
            best_valid = (corr.plate, conf)
    if best_valid:
        return best_valid[0], True
    if options:
        return plates.normalise(options[0][0]) or None, False
    return None, False


def ingest_event(db: Session, payload: dict[str, Any]) -> ProcessResult:
    """Idempotent ingest of one ANPR event (see docs/ANPR.md for the payload)."""
    existing = db.get(AnprEvent, payload["event_id"])
    if existing is not None:
        return ProcessResult(existing, notes=["duplicate_delivery"])

    ts = datetime.fromtimestamp(payload["ts_ms"] / 1000, tz=timezone.utc)
    gate = db.get(Gate, payload["gate_id"])
    if gate is None:
        raise LookupError(f"unknown gate {payload['gate_id']}")
    direction = payload.get("direction") or Direction.IN
    vclass = (payload.get("vehicle_class") or "BIKE").upper()
    ev = AnprEvent(
        id=payload["event_id"], gate_id=gate.id, camera_ids=payload.get("camera_ids") or [],
        direction=direction, wrong_way=bool(payload.get("wrong_way")), vehicle_class=vclass, ts=ts,
        raw_plate=payload.get("plate"), confidence=float(payload.get("confidence") or 0),
        candidates=payload.get("candidates") or [], images=payload.get("images") or {},
        latency_ms=payload.get("latency_ms"), status=EventStatus.REVIEW,
    )
    db.add(ev)

    configured = gate_direction(gate, ts)
    if configured != Direction.BOTH and direction != configured:
        ev.wrong_way = True
    plate, valid = (None, False)
    if payload.get("status", "READ") != "UNREAD":
        plate, valid = _pick_plate(db, payload)
    ev.plate_norm = plate

    if ev.wrong_way:
        ev.status = EventStatus.WRONG_WAY
        alert = Alert(kind="WRONG_WAY", severity="WARN", gate_id=gate.id, event_id=ev.id,
                      message=f"Wrong-way {direction} at {gate.name}: {plate or 'unread'}",
                      data={"plate": plate, "images": ev.images})
        db.add(alert)
        db.flush()
        events.emit(db, "anpr.event", _event_dict(ev))
        events.emit(db, "alert", {"id": alert.id, "kind": alert.kind, "gate_id": gate.id, "message": alert.message})
        return ProcessResult(ev, alert=alert)

    min_conf = float(get_setting(db, "min_confidence"))
    if not plate or (not valid and ev.confidence < min_conf):
        ev.status = EventStatus.UNREAD
        ev.review_reason = "UNREAD"
        db.flush()
        events.emit(db, "anpr.event", _event_dict(ev))
        events.emit(db, "review.new", {"event_id": ev.id, "reason": "UNREAD"})
        result = ProcessResult(ev)
        if direction == Direction.OUT:
            result.exit_display = _exit_display(ev, None, None, state="NEUTRAL")
            events.emit(db, "exit", result.exit_display)
        return result

    vc = db.get(VehicleClass, vclass)
    if vc is None or not vc.enabled:
        ev.status = EventStatus.REVIEW
        ev.review_reason = "CLASS_DISABLED"
        db.flush()
        events.emit(db, "anpr.event", _event_dict(ev))
        return ProcessResult(ev)

    # backend-side de-duplication (safety net behind the ANPR aggregator)
    window = int(get_setting(db, "dedupe_seconds"))
    dup = db.scalars(select(AnprEvent.id).where(
        AnprEvent.gate_id == gate.id, AnprEvent.plate_norm == plate, AnprEvent.direction == direction,
        AnprEvent.id != ev.id, AnprEvent.status != EventStatus.DUPLICATE,
        AnprEvent.ts > ts - timedelta(seconds=window), AnprEvent.ts <= ts + timedelta(seconds=window),
    )).first()
    if dup:
        ev.status = EventStatus.DUPLICATE
        db.flush()
        return ProcessResult(ev, notes=[f"duplicate_of:{dup}"])

    if direction == Direction.IN:
        result = handle_entry(db, ev, plate, vclass)
    else:
        result = handle_exit(db, ev, plate, vclass)
    events.emit(db, "anpr.event", _event_dict(ev))
    return result


def _log_correction(db: Session, ev: Optional[AnprEvent], raw: Optional[str], chosen: str, source: str,
                    distance: Optional[int], user_id: Optional[int] = None, session_id: Optional[int] = None) -> None:
    db.add(PlateCorrection(event_id=ev.id if ev else None, camera_ids=ev.camera_ids if ev else [],
                           raw_plate=raw, chosen_plate=chosen, source=source, distance=distance,
                           user_id=user_id, session_id=session_id))


# ------------------------------------------------------------------ entry
def handle_entry(db: Session, ev: AnprEvent, plate: str, vclass: str, *, manual_user: Optional[int] = None) -> ProcessResult:
    tol = int(get_setting(db, "approx_tolerance"))
    vehicle = find_vehicle(db, plate)
    match = "MANUAL" if manual_user else "EXACT"
    if vehicle is None and not manual_user:
        # a known regular (pass holder / has balance) misread by a character is still them
        best = regular_match(db, ev, plate, vclass, tol)
        if best is not None:
            vehicle = best.vehicle
            match = "APPROX"
            ev.match_distance = best.distance
            _log_correction(db, ev, plate, vehicle.plate, "APPROX_AUTO", best.distance)
    if vehicle is None:
        vehicle = get_or_create_vehicle(db, plate, vclass, ev.ts)
    vehicle.last_seen = max(vehicle.last_seen, ev.ts)
    notes = []

    prev = open_session_for(db, vehicle.id)
    had_pass_inside = False
    if prev is not None:
        had_pass_inside = prev.status == SessionStatus.PASS
        prev.status = SessionStatus.ORPHAN_ENTRY
        prev.closed_at = utcnow()
        prev.note = f"Superseded by entry {ev.id} (missed exit)"
        notes.append(f"orphan_entry:{prev.id}")
        events.emit(db, "review.new", {"session_id": prev.id, "reason": "ORPHAN_ENTRY"})

    zone = zone_for_gate(db, ev.gate_id)
    sess = ParkingSession(vehicle_id=vehicle.id, vehicle_class=vclass, entry_event_id=ev.id, entry_gate=ev.gate_id,
                          entry_at=ev.ts, zone_id=zone.id if zone else None, entry_match=match)
    p = active_pass(db, vehicle.id, ev.ts)
    if p is not None and not (had_pass_inside and get_setting(db, "pass_one_open_session")):
        sess.status = SessionStatus.PASS
        sess.pass_id = p.id
    else:
        sess.status = SessionStatus.OPEN
        sess.tariff_id = get_tariff(db, vclass, ev.ts).id
        if p is not None:
            ev.review_reason = "PASS_ALREADY_INSIDE"
            notes.append("pass_double_entry")
    db.add(sess)
    db.flush()

    ev.status = EventStatus.MATCHED
    ev.vehicle_id = vehicle.id
    ev.session_id = sess.id
    ev.match_type = match
    ev.matched_plate = vehicle.plate
    events.emit(db, "session.opened", session_brief(db, sess, vehicle, ev))
    return ProcessResult(ev, session=sess, notes=notes)


# ------------------------------------------------------------------ exit
def handle_exit(db: Session, ev: AnprEvent, plate: str, vclass: str, *, manual_user: Optional[int] = None,
                forced_session: Optional[ParkingSession] = None) -> ProcessResult:
    tol = int(get_setting(db, "approx_tolerance"))
    vehicle = find_vehicle(db, plate)
    sess: Optional[ParkingSession] = forced_session
    match = "MANUAL" if (manual_user or forced_session) else "EXACT"
    if sess is not None:
        vehicle = db.get(Vehicle, sess.vehicle_id)
    if sess is None and vehicle is not None:
        sess = open_session_for(db, vehicle.id)
        if sess is not None and sess.entry_at and sess.entry_at > ev.ts:
            sess = None
    if sess is None:
        cands = [c for c in approx_open_sessions(db, plate, vclass, ev.ts, tol)
                 if vehicle is None or c.vehicle.id != vehicle.id]
        if vehicle is not None:
            # exact plate known but no open session: only take a *closer* approx session
            cands = [c for c in cands if c.distance == 0]
        best = unique_best(cands)
        if best is not None:
            sess, vehicle = best.session, best.vehicle
            match = "MANUAL" if manual_user else "APPROX"
            ev.match_distance = best.distance
            _log_correction(db, ev, plate, vehicle.plate, "REVIEW" if manual_user else "APPROX_AUTO", best.distance,
                            session_id=sess.id, user_id=manual_user)
            if manual_user or _exit_read_is_truth(db, ev, plate, sess):
                vehicle = rehome_session(db, sess, plate, user_id=manual_user,
                                         reason=f"entry misread {vehicle.plate}; exit read {plate}")
        elif len(cands) > 1 and not manual_user:
            ev.status = EventStatus.REVIEW
            ev.review_reason = "AMBIGUOUS"
            ev.candidates = list(ev.candidates or []) + [
                {"plate": c.vehicle.plate, "session_id": c.session.id, "distance": c.distance, "source": "open_session"}
                for c in cands[:5]]
            db.flush()
            events.emit(db, "review.new", {"event_id": ev.id, "reason": "AMBIGUOUS"})
            disp = _exit_display(ev, None, None, state="NEUTRAL")
            events.emit(db, "exit", disp)
            return ProcessResult(ev, exit_display=disp)

    if sess is None and vehicle is None and not manual_user:
        best = regular_match(db, ev, plate, vclass, tol)
        if best is not None:
            vehicle, match = best.vehicle, "APPROX"
            ev.match_distance = best.distance
            _log_correction(db, ev, plate, vehicle.plate, "APPROX_AUTO", best.distance)

    if sess is None:
        if vehicle is None:
            if manual_user:
                vehicle = get_or_create_vehicle(db, plate, vclass, ev.ts)
            else:
                ev.status = EventStatus.REVIEW
                ev.review_reason = "NO_MATCH"
                db.flush()
                events.emit(db, "review.new", {"event_id": ev.id, "reason": "NO_MATCH"})
                disp = _exit_display(ev, None, None, state="NEUTRAL")
                events.emit(db, "exit", disp)
                return ProcessResult(ev, exit_display=disp)
        # known vehicle leaving without a recorded entry
        sess = ParkingSession(vehicle_id=vehicle.id, vehicle_class=vclass, exit_event_id=ev.id, exit_gate=ev.gate_id,
                              exit_at=ev.ts, status=SessionStatus.ORPHAN_EXIT, exit_match=match, closed_at=utcnow(),
                              note="Exit without recorded entry")
        p = active_pass(db, vehicle.id, ev.ts)
        if p is not None:
            sess.pass_id = p.id
        db.add(sess)
        db.flush()
        vehicle.last_seen = max(vehicle.last_seen, ev.ts)
        ev.status = EventStatus.MATCHED
        ev.vehicle_id, ev.session_id, ev.match_type, ev.matched_plate = vehicle.id, sess.id, match, vehicle.plate
        if p is None:
            events.emit(db, "review.new", {"session_id": sess.id, "reason": "ORPHAN_EXIT"})
        state, alert = _exit_state_and_alert(db, ev, vehicle, sess)
        disp = _exit_display(ev, vehicle, sess, state=state, pass_=p)
        events.emit(db, "exit", disp)
        return ProcessResult(ev, session=sess, exit_display=disp, alert=alert)

    sess.exit_event_id = ev.id
    sess.exit_gate = ev.gate_id
    sess.exit_match = match
    close_session(db, sess, ev.ts)
    vehicle.last_seen = max(vehicle.last_seen, ev.ts)
    ev.status = EventStatus.MATCHED
    ev.vehicle_id, ev.session_id, ev.match_type, ev.matched_plate = vehicle.id, sess.id, match, vehicle.plate
    p = db.get(Pass, sess.pass_id) if sess.pass_id else active_pass(db, vehicle.id, ev.ts)
    state, alert = _exit_state_and_alert(db, ev, vehicle, sess)
    disp = _exit_display(ev, vehicle, sess, state=state, pass_=p)
    events.emit(db, "exit", disp)
    events.emit(db, "session.closed", session_brief(db, sess, vehicle, ev))
    _settlement_message(db, vehicle, sess)
    return ProcessResult(ev, session=sess, exit_display=disp, alert=alert)


def close_session(db: Session, sess: ParkingSession, exit_at: datetime, *, user_id: Optional[int] = None,
                  note: Optional[str] = None) -> int:
    """Close a session at `exit_at`, post the CHARGE (tariff at entry). Returns the charge."""
    sess.exit_at = exit_at
    sess.closed_at = utcnow()
    tz = site_tz()
    charge = 0
    if sess.status == SessionStatus.PASS:
        p = db.get(Pass, sess.pass_id)
        if p is not None and p.ends_at < exit_at:
            # pass expired during the stay: normal tariff from the expiry instant
            start = max(p.ends_at, sess.entry_at or p.ends_at)
            t = get_tariff(db, sess.vehicle_class, start)
            sess.tariff_id = t.id
            charge = calculate_charge(sess.vehicle_class, start, exit_at, t, tz)
    else:
        t = db.get(Tariff, sess.tariff_id) if sess.tariff_id else None
        if t is None:
            t = get_tariff(db, sess.vehicle_class, sess.entry_at)
            sess.tariff_id = t.id
        charge = calculate_charge(sess.vehicle_class, sess.entry_at, exit_at, t, tz)
    sess.charge_paise = charge
    if charge > 0:
        ledger.post(db, sess.vehicle_id, LedgerKind.CHARGE, charge, session_id=sess.id, user_id=user_id, reason=note)
    veh = db.get(Vehicle, sess.vehicle_id)
    if sess.status == SessionStatus.PASS and charge == 0:
        sess.status = SessionStatus.CLOSED
    else:
        sess.status = SessionStatus.SETTLED if veh.balance_paise <= 0 else SessionStatus.CLOSED
    db.flush()
    return charge


def _exit_state_and_alert(db: Session, ev: AnprEvent, vehicle: Vehicle, sess: ParkingSession) -> tuple[str, Optional[Alert]]:
    threshold = int(get_setting(db, "alert_balance_threshold_paise"))
    effective = vehicle.balance_paise - pending_claims_paise(db, vehicle.id)
    paid_for_session = session_paid_paise(db, sess.id)
    charge = sess.charge_paise or 0
    unpaid_session = charge > 0 and paid_for_session == 0 and effective > 0
    if not (unpaid_session or effective > threshold):
        return "GREEN", None
    alert = Alert(kind="EXIT_UNPAID", severity="WARN", gate_id=ev.gate_id, session_id=sess.id, vehicle_id=vehicle.id,
                  event_id=ev.id, message=f"{vehicle.display_plate} leaving with ₹{effective / 100:.0f} due",
                  data={"plate": vehicle.plate, "display_plate": vehicle.display_plate, "amount_due_paise": effective,
                        "charge_paise": charge, "paid_paise": paid_for_session, "images": ev.images,
                        "plate_image": image_url((ev.images or {}).get("plate_crop"))})
    db.add(alert)
    db.flush()
    events.emit(db, "alert", {"id": alert.id, "kind": alert.kind, "gate_id": ev.gate_id, "message": alert.message,
                              "session_id": sess.id, "vehicle_id": vehicle.id, **alert.data})
    return "RED", alert


def _exit_display(ev: AnprEvent, vehicle: Optional[Vehicle], sess: Optional[ParkingSession], *, state: str,
                  pass_: Optional[Pass] = None) -> dict:
    d: dict[str, Any] = {
        "gate_id": ev.gate_id, "event_id": ev.id, "ts": ev.ts.isoformat(), "state": state,
        "plate": vehicle.plate if vehicle else ev.plate_norm, "display_plate": vehicle.display_plate if vehicle else (ev.plate_norm or "—"),
        "vehicle_class": ev.vehicle_class, "session_id": sess.id if sess else None,
        "plate_image": image_url((ev.images or {}).get("plate_crop")),
        "frame_image": image_url((ev.images or {}).get("full_frame")),
        "amount_due_paise": max(0, vehicle.balance_paise) if vehicle else 0,
        "pass_valid_till": None, "pass_days_left": None,
    }
    if pass_ is not None and pass_.ends_at >= ev.ts:
        local_end = pass_.ends_at.astimezone(site_tz())
        d["pass_valid_till"] = local_end.date().isoformat()
        d["pass_days_left"] = max(0, (local_end.date() - ev.ts.astimezone(site_tz()).date()).days)
    return d


def _settlement_message(db: Session, vehicle: Vehicle, sess: ParkingSession) -> None:
    if not vehicle.phone or sess.status == SessionStatus.PASS:
        return
    paid = session_paid_paise(db, sess.id, include_claimed=False)
    if (sess.charge_paise or 0) == paid:
        return
    from . import notify

    notify.settlement(db, vehicle, sess)


def _event_dict(ev: AnprEvent) -> dict:
    return {"id": ev.id, "gate_id": ev.gate_id, "direction": ev.direction, "status": ev.status,
            "plate": ev.plate_norm, "raw_plate": ev.raw_plate, "confidence": ev.confidence, "ts": ev.ts.isoformat(),
            "vehicle_class": ev.vehicle_class, "camera_ids": ev.camera_ids, "match_type": ev.match_type,
            "review_reason": ev.review_reason, "session_id": ev.session_id,
            "plate_image": image_url((ev.images or {}).get("plate_crop"))}


def session_brief(db: Session, sess: ParkingSession, vehicle: Vehicle, ev: Optional[AnprEvent] = None) -> dict:
    return {"session_id": sess.id, "vehicle_id": vehicle.id, "plate": vehicle.plate, "display_plate": vehicle.display_plate,
            "vehicle_class": sess.vehicle_class, "status": sess.status, "entry_at": sess.entry_at.isoformat() if sess.entry_at else None,
            "exit_at": sess.exit_at.isoformat() if sess.exit_at else None, "gate_id": sess.entry_gate,
            "zone_id": sess.zone_id, "balance_paise": vehicle.balance_paise, "charge_paise": sess.charge_paise,
            "plate_image": image_url((ev.images or {}).get("plate_crop")) if ev else None}


# ------------------------------------------------------------------ manual review
def resolve_event(db: Session, event_id: str, *, user_id: int, plate: Optional[str] = None,
                  session_id: Optional[int] = None, discard: bool = False, note: Optional[str] = None) -> ProcessResult:
    """Supervisor resolves an UNREAD / REVIEW event by typing/picking the plate or a session."""
    ev = db.get(AnprEvent, event_id)
    if ev is None:
        raise LookupError("event not found")
    if ev.status not in (EventStatus.UNREAD, EventStatus.REVIEW):
        raise ValueError(f"event is {ev.status}, not awaiting review")
    ev.reviewed_by, ev.reviewed_at, ev.review_note = user_id, utcnow(), note
    if discard:
        ev.status = EventStatus.DISCARDED
        db.flush()
        return ProcessResult(ev)
    forced = db.get(ParkingSession, session_id) if session_id else None
    if forced is not None:
        plate = db.get(Vehicle, forced.vehicle_id).plate
    norm = plates.normalise(plate)
    if not norm:
        raise ValueError("plate required")
    corr = plates.correct(norm, get_setting(db, "state_codes"))
    norm = corr.plate if corr.valid else norm
    _log_correction(db, ev, ev.raw_plate, norm, "REVIEW", None, user_id=user_id)
    ev.plate_norm = norm
    vclass = ev.vehicle_class if ev.vehicle_class in ("BIKE", "CAR") else "BIKE"
    if ev.direction == Direction.IN:
        res = handle_entry(db, ev, norm, vclass, manual_user=user_id)
    else:
        res = handle_exit(db, ev, norm, vclass, manual_user=user_id, forced_session=forced)
    ev.status = EventStatus.RESOLVED
    ev.match_type = "MANUAL"
    db.flush()
    return res


def resolve_orphan(db: Session, session_id: int, *, user_id: int, action: str, at: Optional[datetime] = None,
                   note: str) -> ParkingSession:
    """ORPHAN_ENTRY: CHARGE with an estimated exit time, or WAIVE. ORPHAN_EXIT: CHARGE with estimated entry, or WAIVE."""
    if not note or not note.strip():
        raise ValueError("note required")
    sess = db.get(ParkingSession, session_id)
    if sess is None or sess.status not in (SessionStatus.ORPHAN_ENTRY, SessionStatus.ORPHAN_EXIT):
        raise ValueError("not an orphan session")
    if action == "WAIVE":
        sess.status = SessionStatus.CLOSED
        sess.charge_paise = 0
        sess.note = f"{sess.note or ''} | waived: {note}".strip(" |")
    elif action == "CHARGE":
        if at is None:
            raise ValueError("time required")
        if sess.status == SessionStatus.ORPHAN_ENTRY:
            if sess.pass_id:
                sess.status = SessionStatus.PASS
            else:
                if not sess.tariff_id:
                    sess.tariff_id = get_tariff(db, sess.vehicle_class, sess.entry_at).id
                sess.status = SessionStatus.OPEN
            close_session(db, sess, at, user_id=user_id, note=note)
        else:
            sess.entry_at = at
            sess.tariff_id = get_tariff(db, sess.vehicle_class, at).id
            exit_at = sess.exit_at
            sess.status = SessionStatus.OPEN
            close_session(db, sess, exit_at, user_id=user_id, note=note)
        sess.note = f"{sess.note or ''} | resolved: {note}".strip(" |")
    else:
        raise ValueError("action must be CHARGE or WAIVE")
    db.flush()
    return sess


def correct_session_plate(db: Session, session_id: int, plate: str, *, user_id: int) -> ParkingSession:
    """Worker says the ANPR read is wrong: move an unpaid open session to the right vehicle.
    The correction is logged (ANPR accuracy data) and the entry event is flagged for review."""
    s = db.get(ParkingSession, session_id)
    if s is None or s.status not in (SessionStatus.OPEN, SessionStatus.PREPAID, SessionStatus.PASS):
        raise ValueError("only open sessions can be corrected")
    norm = plates.normalise(plate)
    corr = plates.correct(norm, get_setting(db, "state_codes"))
    norm = corr.plate if corr.valid else norm
    if not norm or len(norm) < 6:
        raise ValueError("enter the full plate")
    old = db.get(Vehicle, s.vehicle_id)
    if old.plate == norm:
        return s
    if db.scalar(select(func.count(Payment.id)).where(Payment.session_id == s.id)):
        raise ValueError("session already has a payment; ask a supervisor")
    target = get_or_create_vehicle(db, norm, s.vehicle_class, s.entry_at)
    if open_session_for(db, target.id) is not None:
        raise ValueError(f"{target.display_plate} already has an open session; ask a supervisor")
    ev = db.get(AnprEvent, s.entry_event_id) if s.entry_event_id else None
    db.add(PlateCorrection(event_id=ev.id if ev else None, session_id=s.id, camera_ids=ev.camera_ids if ev else [],
                           raw_plate=old.plate, chosen_plate=norm, source="WORKER", user_id=user_id))
    s.vehicle_id = target.id
    p = active_pass(db, target.id, s.entry_at)
    if p is not None:
        s.status, s.pass_id, s.tariff_id = SessionStatus.PASS, p.id, None
    elif s.status == SessionStatus.PASS:
        s.status, s.pass_id = SessionStatus.OPEN, None
        s.tariff_id = get_tariff(db, s.vehicle_class, s.entry_at).id
    if ev is not None:
        ev.review_reason = "WORKER_CORRECTED"
        ev.matched_plate = norm
        ev.vehicle_id = target.id
    target.last_seen = max(target.last_seen, s.entry_at)
    db.flush()
    return s


def _exit_read_is_truth(db: Session, ev: AnprEvent, plate: str, sess: ParkingSession) -> bool:
    """The exit read is more trustworthy than the entry read that created a one-off vehicle."""
    if find_vehicle(db, plate) is not None or not plates.is_valid(plate, get_setting(db, "state_codes")):
        return False
    if ev.confidence < float(get_setting(db, "approx_regular_max_confidence")):
        return False
    entry = db.get(AnprEvent, sess.entry_event_id) if sess.entry_event_id else None
    if entry is None or entry.match_type != "EXACT" or entry.confidence >= ev.confidence:
        return False
    ghost = db.get(Vehicle, sess.vehicle_id)
    born_here = ghost.first_seen >= (sess.entry_at or ghost.first_seen) - timedelta(minutes=1)
    others = db.scalar(select(func.count(ParkingSession.id)).where(ParkingSession.vehicle_id == ghost.id,
                                                                   ParkingSession.id != sess.id)) or 0
    return born_here and others == 0 and sess.pass_id is None


def rehome_session(db: Session, sess: ParkingSession, plate: str, *, user_id: Optional[int], reason: str) -> Vehicle:
    """Move a session that was opened under a misread plate to the true vehicle. Payments already
    made for the session follow it as a balanced ADJUSTMENT pair (the ledger stays append-only)."""
    ghost = db.get(Vehicle, sess.vehicle_id)
    target = get_or_create_vehicle(db, plate, sess.vehicle_class, sess.entry_at or utcnow())
    if target.id == ghost.id:
        return target
    paid = int(db.scalar(select(func.coalesce(func.sum(Payment.amount_paise), 0)).where(
        Payment.session_id == sess.id, Payment.status == PayStatus.CONFIRMED)) or 0)
    if paid:
        note = f"session #{sess.id} moved {ghost.plate} -> {target.plate}: {reason}"
        ledger.post(db, ghost.id, LedgerKind.ADJUSTMENT, paid, session_id=sess.id, reason=note, user_id=user_id)
        ledger.post(db, target.id, LedgerKind.ADJUSTMENT, -paid, session_id=sess.id, reason=note, user_id=user_id)
    for pm in db.scalars(select(Payment).where(Payment.session_id == sess.id)).all():
        pm.status_note = ((pm.status_note or "") + f" [session re-homed to {target.plate}]").strip()
    sess.vehicle_id = target.id
    if sess.entry_at:
        target.first_seen = min(target.first_seen, sess.entry_at)
    p = active_pass(db, target.id, sess.entry_at or utcnow())
    if p is not None and sess.status in (SessionStatus.OPEN, SessionStatus.PREPAID):
        sess.status, sess.pass_id = SessionStatus.PASS, p.id
    db.flush()
    return target
