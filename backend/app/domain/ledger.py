"""Append-only per-vehicle ledger. balance = sum(amount); positive = customer owes."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import LedgerEntry, LedgerKind, Vehicle


def post(
    db: Session,
    vehicle_id: int,
    kind: str,
    amount_paise: int,
    *,
    session_id: Optional[int] = None,
    payment_id: Optional[int] = None,
    pass_id: Optional[int] = None,
    reason: Optional[str] = None,
    user_id: Optional[int] = None,
) -> LedgerEntry:
    if kind == LedgerKind.ADJUSTMENT and not (reason and reason.strip()):
        raise ValueError("adjustments need a reason")
    if kind == LedgerKind.PAYMENT and amount_paise > 0:
        raise ValueError("payments must be posted as negative amounts")
    if kind in (LedgerKind.CHARGE, LedgerKind.PASS_SALE) and amount_paise < 0:
        raise ValueError("charges must be positive")
    entry = LedgerEntry(
        vehicle_id=vehicle_id, kind=kind, amount_paise=amount_paise, session_id=session_id,
        payment_id=payment_id, pass_id=pass_id, reason=reason, created_by=user_id,
    )
    db.add(entry)
    veh = db.get(Vehicle, vehicle_id)
    # SQL-side increment: safe under concurrent requests on PostgreSQL
    veh.balance_paise = Vehicle.balance_paise + amount_paise
    db.flush()
    db.refresh(veh, ["balance_paise"])
    return entry


def balance(db: Session, vehicle_id: int) -> int:
    """Authoritative balance computed from the ledger."""
    return int(db.scalar(select(func.coalesce(func.sum(LedgerEntry.amount_paise), 0))
                         .where(LedgerEntry.vehicle_id == vehicle_id)) or 0)


def verify_balances(db: Session) -> list[tuple[int, int, int]]:
    """Vehicles whose cached balance disagrees with the ledger: (vehicle_id, cached, ledger)."""
    sums = dict(db.execute(select(LedgerEntry.vehicle_id, func.sum(LedgerEntry.amount_paise))
                           .group_by(LedgerEntry.vehicle_id)).all())
    bad = []
    for vid, cached in db.execute(select(Vehicle.id, Vehicle.balance_paise)).all():
        real = int(sums.get(vid, 0) or 0)
        if real != (cached or 0):
            bad.append((vid, cached, real))
    return bad
