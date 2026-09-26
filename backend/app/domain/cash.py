"""Cash control: shifts, live cash-in-hand, two-party handovers, bank deposits, reconciliation."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import events
from ..db import utcnow
from ..models import BankDeposit, CashHandover, Payment, PayMode, PayStatus, Role, Shift, User, ZoneAssignment
from .lookup import day_bounds
from .settings import get_setting

DENOMINATIONS = (1, 2, 5, 10, 20, 50, 100, 200, 500)


class CashError(ValueError):
    pass


# ------------------------------------------------------------------ shifts
def current_shift(db: Session, user_id: int, at: Optional[datetime] = None) -> Optional[Shift]:
    stmt = select(Shift).where(Shift.user_id == user_id)
    if at is None:
        stmt = stmt.where(Shift.status == "OPEN")
    else:
        stmt = stmt.where(Shift.opened_at <= at, (Shift.closed_at.is_(None)) | (Shift.closed_at > at))
    return db.scalars(stmt.order_by(Shift.opened_at.desc())).first()


def open_shift(db: Session, user: User, zone_id: Optional[int] = None, at: Optional[datetime] = None) -> Shift:
    existing = current_shift(db, user.id)
    if existing is not None:
        return existing
    at = at or utcnow()
    if zone_id is None:
        za = db.scalars(select(ZoneAssignment).where(ZoneAssignment.user_id == user.id, ZoneAssignment.starts_at <= at,
                                                     ZoneAssignment.ends_at > at)).first()
        zone_id = za.zone_id if za else None
    s = Shift(user_id=user.id, zone_id=zone_id, opened_at=at, opening_cash_paise=0, status="OPEN")
    db.add(s)
    db.flush()
    return s


def shift_totals(db: Session, shift_id: int) -> dict:
    rows = db.execute(select(Payment.mode, func.coalesce(func.sum(Payment.amount_paise), 0), func.count(Payment.id))
                      .where(Payment.shift_id == shift_id, Payment.status.in_([PayStatus.CONFIRMED, PayStatus.CLAIMED_OFFLINE]))
                      .group_by(Payment.mode)).all()
    out = {"upi_paise": 0, "cash_paise": 0, "upi_count": 0, "cash_count": 0}
    for mode, amt, cnt in rows:
        k = "upi" if mode == PayMode.UPI else "cash"
        out[f"{k}_paise"] += int(amt)
        out[f"{k}_count"] += int(cnt)
    out["handed_over_paise"] = int(db.scalar(select(func.coalesce(func.sum(CashHandover.counted_paise), 0)).where(
        CashHandover.shift_id == shift_id, CashHandover.status == "CONFIRMED")) or 0)
    out["pending_handover_paise"] = int(db.scalar(select(func.coalesce(func.sum(CashHandover.declared_paise), 0)).where(
        CashHandover.shift_id == shift_id, CashHandover.status == "PENDING")) or 0)
    return out


def close_shift(db: Session, user: User, note: Optional[str] = None, at: Optional[datetime] = None) -> Shift:
    s = current_shift(db, user.id)
    if s is None:
        raise CashError("no open shift")
    t = shift_totals(db, s.id)
    if t["pending_handover_paise"]:
        raise CashError("a cash handover is waiting for supervisor confirmation")
    held = t["cash_paise"] - t["handed_over_paise"]
    s.upi_total_paise, s.cash_total_paise, s.handed_over_paise = t["upi_paise"], t["cash_paise"], t["handed_over_paise"]
    s.variance_paise = -held  # negative = cash not handed over
    if held and not (note and note.strip()):
        raise CashError(f"₹{held / 100:.0f} cash still in hand: hand it over, or add a note to close with a variance")
    s.closed_at, s.status, s.note = at or utcnow(), "CLOSED", note
    db.flush()
    events.emit(db, "shift.closed", {"shift_id": s.id, "user_id": user.id, "variance_paise": s.variance_paise})
    return s


# ------------------------------------------------------------------ cash in hand
def cash_in_hand(db: Session, user_id: int, shift_id: Optional[int] = None) -> int:
    if shift_id is None:
        s = current_shift(db, user_id)
        if s is None:
            return 0
        shift_id = s.id
    collected = db.scalar(select(func.coalesce(func.sum(Payment.amount_paise), 0)).where(
        Payment.shift_id == shift_id, Payment.mode == PayMode.CASH, Payment.status == PayStatus.CONFIRMED)) or 0
    handed = db.scalar(select(func.coalesce(func.sum(CashHandover.counted_paise), 0)).where(
        CashHandover.shift_id == shift_id, CashHandover.status == "CONFIRMED")) or 0
    return int(collected) - int(handed)


def holding_dict(db: Session, user_id: int) -> dict:
    s = current_shift(db, user_id)
    held = cash_in_hand(db, user_id, s.id) if s else 0
    limit = int(get_setting(db, "cash_limit_paise"))
    warn_at = int(limit * float(get_setting(db, "cash_warn_ratio")))
    u = db.get(User, user_id)
    return {"user_id": user_id, "name": u.name if u else None, "shift_id": s.id if s else None,
            "zone_id": s.zone_id if s else None, "cash_in_hand_paise": held, "limit_paise": limit,
            "warn": held >= warn_at, "blocked": held >= limit}


def all_holdings(db: Session) -> list[dict]:
    uids = db.scalars(select(Shift.user_id).where(Shift.status == "OPEN")).all()
    return [holding_dict(db, uid) for uid in sorted(set(uids))]


# ------------------------------------------------------------------ handovers
def _denoms_total(denoms: dict) -> int:
    total = 0
    for k, n in (denoms or {}).items():
        d = int(k)
        if d not in DENOMINATIONS or int(n) < 0:
            raise CashError(f"invalid denomination {k}")
        total += d * 100 * int(n)
    return total


def declare_handover(db: Session, worker: User, amount_paise: int, denoms: dict, *, to_user: Optional[int] = None,
                     client_uuid: Optional[str] = None) -> CashHandover:
    if client_uuid:
        ex = db.scalars(select(CashHandover).where(CashHandover.client_uuid == client_uuid)).first()
        if ex is not None:
            return ex
    s = current_shift(db, worker.id)
    if s is None:
        raise CashError("no open shift")
    if _denoms_total(denoms) != amount_paise:
        raise CashError("denomination count does not add up to the declared amount")
    if amount_paise <= 0:
        raise CashError("nothing to hand over")
    pending = db.scalar(select(func.count(CashHandover.id)).where(CashHandover.shift_id == s.id, CashHandover.status == "PENDING"))
    if pending:
        raise CashError("a handover is already pending")
    h = CashHandover(from_user=worker.id, to_user=to_user, shift_id=s.id, expected_paise=cash_in_hand(db, worker.id, s.id),
                     declared_paise=amount_paise, declared_denoms={str(k): int(v) for k, v in denoms.items()},
                     client_uuid=client_uuid)
    db.add(h)
    db.flush()
    events.emit(db, "handover.pending", handover_dict(db, h))
    return h


def confirm_handover(db: Session, handover_id: int, supervisor: User, counted_denoms: dict, *,
                     photo_path: Optional[str] = None, note: Optional[str] = None) -> CashHandover:
    if supervisor.role not in (Role.SUPERVISOR, Role.ADMIN):
        raise PermissionError("supervisor only")
    h = db.get(CashHandover, handover_id)
    if h is None or h.status != "PENDING":
        raise CashError("handover not pending")
    if h.from_user == supervisor.id:
        raise CashError("a different person must count the cash")
    counted = _denoms_total(counted_denoms)
    variance = counted - h.declared_paise
    if variance and not (note and note.strip()):
        raise CashError("count differs from declaration: a note is required")
    if not photo_path:
        raise CashError("photo of the counted cash is required")
    h.counted_paise, h.counted_denoms = counted, {str(k): int(v) for k, v in counted_denoms.items()}
    h.variance_paise, h.status, h.to_user = variance, "CONFIRMED", supervisor.id
    h.photo_path, h.note, h.confirmed_at = photo_path, note, utcnow()
    s = db.get(Shift, h.shift_id)
    if s is not None:
        s.handed_over_paise = (s.handed_over_paise or 0) + counted
    db.flush()
    events.emit(db, "handover.confirmed", handover_dict(db, h))
    events.emit(db, "cash.updated", holding_dict(db, h.from_user))
    return h


def reject_handover(db: Session, handover_id: int, supervisor: User, note: str) -> CashHandover:
    h = db.get(CashHandover, handover_id)
    if h is None or h.status != "PENDING":
        raise CashError("handover not pending")
    h.status, h.note, h.to_user, h.confirmed_at = "REJECTED", note, supervisor.id, utcnow()
    db.flush()
    return h


def handover_dict(db: Session, h: CashHandover) -> dict:
    fu = db.get(User, h.from_user)
    return {"id": h.id, "from_user": h.from_user, "from_name": fu.name if fu else None, "to_user": h.to_user,
            "shift_id": h.shift_id, "expected_paise": h.expected_paise, "declared_paise": h.declared_paise,
            "declared_denoms": h.declared_denoms, "counted_paise": h.counted_paise, "counted_denoms": h.counted_denoms,
            "variance_paise": h.variance_paise, "status": h.status, "photo_path": h.photo_path, "note": h.note,
            "declared_at": h.declared_at.isoformat(), "confirmed_at": h.confirmed_at.isoformat() if h.confirmed_at else None}


# ------------------------------------------------------------------ bank deposit & reconciliation
def record_deposit(db: Session, supervisor: User, business_date: str, amount_paise: int, slip_ref: str,
                   slip_photo_path: Optional[str], note: Optional[str] = None) -> BankDeposit:
    if supervisor.role not in (Role.SUPERVISOR, Role.ADMIN):
        raise PermissionError("supervisor only")
    if amount_paise <= 0 or not slip_ref:
        raise CashError("amount and deposit slip reference are required")
    d = BankDeposit(business_date=business_date, amount_paise=amount_paise, slip_ref=slip_ref,
                    slip_photo_path=slip_photo_path, deposited_by=supervisor.id, note=note)
    db.add(d)
    db.flush()
    return d


def mark_bank_credit(db: Session, deposit_id: int, credited_paise: int, note: Optional[str] = None) -> BankDeposit:
    d = db.get(BankDeposit, deposit_id)
    d.bank_credited_paise = credited_paise
    d.bank_status = "CREDITED" if credited_paise == d.amount_paise else "MISMATCH"
    d.reconciled_at = utcnow()
    if note:
        d.note = note
    db.flush()
    return d


def cash_reconciliation(db: Session, business_date: str) -> dict:
    """System cash collected -> handed over to supervisor -> deposited -> credited by bank."""
    start, end = day_bounds(business_date)
    collected = int(db.scalar(select(func.coalesce(func.sum(Payment.amount_paise), 0)).where(
        Payment.mode == PayMode.CASH, Payment.status == PayStatus.CONFIRMED,
        Payment.created_at >= start, Payment.created_at < end)) or 0)
    reversed_ = int(db.scalar(select(func.coalesce(func.sum(Payment.amount_paise), 0)).where(
        Payment.mode == PayMode.CASH, Payment.status == PayStatus.REVERSED,
        Payment.created_at >= start, Payment.created_at < end)) or 0)
    handed = int(db.scalar(select(func.coalesce(func.sum(CashHandover.counted_paise), 0)).where(
        CashHandover.status == "CONFIRMED", CashHandover.confirmed_at >= start, CashHandover.confirmed_at < end)) or 0)
    variance = int(db.scalar(select(func.coalesce(func.sum(CashHandover.variance_paise), 0)).where(
        CashHandover.status == "CONFIRMED", CashHandover.confirmed_at >= start, CashHandover.confirmed_at < end)) or 0)
    deps = db.scalars(select(BankDeposit).where(BankDeposit.business_date == business_date)).all()
    deposited = sum(d.amount_paise for d in deps)
    credited = sum(d.bank_credited_paise or 0 for d in deps)
    per_worker = []
    for uid, amt in db.execute(select(Payment.collected_by, func.sum(Payment.amount_paise)).where(
            Payment.mode == PayMode.CASH, Payment.status == PayStatus.CONFIRMED,
            Payment.created_at >= start, Payment.created_at < end).group_by(Payment.collected_by)).all():
        h = int(db.scalar(select(func.coalesce(func.sum(CashHandover.counted_paise), 0)).where(
            CashHandover.from_user == uid, CashHandover.status == "CONFIRMED",
            CashHandover.confirmed_at >= start, CashHandover.confirmed_at < end)) or 0)
        u = db.get(User, uid) if uid else None
        per_worker.append({"user_id": uid, "name": u.name if u else None, "collected_paise": int(amt),
                           "handed_over_paise": h, "gap_paise": int(amt) - h})
    return {
        "date": business_date, "collected_paise": collected, "reversed_paise": reversed_,
        "handed_over_paise": handed, "handover_variance_paise": variance,
        "deposited_paise": deposited, "bank_credited_paise": credited,
        "gap_collected_vs_handed_paise": collected - handed,
        "gap_handed_vs_deposited_paise": handed - deposited,
        "gap_deposited_vs_credited_paise": (deposited - credited) if deps and all(d.bank_status != "PENDING" for d in deps) else None,
        "deposits": [{"id": d.id, "amount_paise": d.amount_paise, "slip_ref": d.slip_ref, "bank_status": d.bank_status,
                      "bank_credited_paise": d.bank_credited_paise} for d in deps],
        "per_worker": per_worker,
    }
