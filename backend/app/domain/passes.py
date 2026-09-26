"""Monthly (or other period) passes: window calculation, activation, expiry, reminders, candidates."""
from __future__ import annotations

import calendar
from datetime import datetime, time, timedelta
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import events
from ..config import get_settings
from ..db import utcnow
from ..models import LedgerKind, ParkingSession, Pass, PassType, Payment, SessionStatus, Vehicle
from . import ledger, notify
from .lookup import site_tz
from .settings import get_setting


def _add_months(d: datetime, months: int) -> datetime:
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    day = min(d.day, calendar.monthrange(y, m)[1])
    return d.replace(year=y, month=m, day=day)


def pass_window(db: Session, vehicle_id: int, pt: PassType, at: datetime) -> tuple[datetime, datetime]:
    """Start today (local midnight) or, for a renewal, when the current pass ends."""
    tz = site_tz()
    current = db.scalars(select(Pass).where(Pass.vehicle_id == vehicle_id, Pass.status == "ACTIVE",
                                            Pass.ends_at > at).order_by(Pass.ends_at.desc())).first()
    if current is not None:
        start = current.ends_at.astimezone(tz)
    else:
        start = datetime.combine(at.astimezone(tz).date(), time(0), tzinfo=tz)
    if pt.period_unit == "MONTH":
        end = _add_months(start, pt.period_value)
    else:
        end = start + timedelta(days=pt.period_value)
    return start, end


def activate_pass(db: Session, p: Pass, payment: Payment) -> Pass:
    if p.status == "ACTIVE":
        return p
    p.status = "ACTIVE"
    p.payment_id = payment.id
    ledger.post(db, p.vehicle_id, LedgerKind.PASS_SALE, p.amount_paise, pass_id=p.id, payment_id=payment.id,
                user_id=payment.collected_by)
    # vehicle currently inside without paying (entered today): its stay is now covered by the pass
    for s in db.scalars(select(ParkingSession).where(ParkingSession.vehicle_id == p.vehicle_id,
                                                     ParkingSession.status == SessionStatus.OPEN,
                                                     ParkingSession.entry_at >= p.starts_at,
                                                     ParkingSession.entry_at < p.ends_at)).all():
        s.status = SessionStatus.PASS
        s.pass_id = p.id
    db.flush()
    events.emit(db, "pass.activated", {"pass_id": p.id, "vehicle_id": p.vehicle_id,
                                       "ends_at": p.ends_at.isoformat()})
    return p


def expire_passes(db: Session, now: Optional[datetime] = None) -> int:
    now = now or utcnow()
    rows = db.scalars(select(Pass).where(Pass.status == "ACTIVE", Pass.ends_at <= now)).all()
    for p in rows:
        p.status = "EXPIRED"
    # unpaid pending passes older than a day are abandoned
    for p in db.scalars(select(Pass).where(Pass.status == "PENDING", Pass.created_at < now - timedelta(days=1))).all():
        p.status = "CANCELLED"
    db.flush()
    return len(rows)


def send_reminders(db: Session, now: Optional[datetime] = None) -> int:
    now = now or utcnow()
    tz = site_tz()
    days = sorted(get_setting(db, "pass_reminder_days") or [5, 1], reverse=True)
    sent = 0
    base = get_settings().public_receipt_base.rsplit("/", 1)[0]
    for p in db.scalars(select(Pass).where(Pass.status == "ACTIVE", Pass.ends_at > now,
                                           Pass.ends_at <= now + timedelta(days=max(days) + 1))).all():
        veh = db.get(Vehicle, p.vehicle_id)
        if not veh.phone or not p.auto_renew_reminders:
            continue
        renewed = db.scalar(select(func.count(Pass.id)).where(Pass.vehicle_id == p.vehicle_id, Pass.id != p.id,
                                                              Pass.status == "ACTIVE", Pass.starts_at >= p.ends_at))
        if renewed:
            continue
        left = (p.ends_at.astimezone(tz).date() - now.astimezone(tz).date()).days
        link = f"{base}/pass?plate={veh.plate}"
        ends_on = p.ends_at.astimezone(tz).date().isoformat()
        if left <= 1 and not p.remind_1d_sent:
            notify.pass_reminder(db, veh, left, ends_on, link)
            p.remind_1d_sent = p.remind_5d_sent = True
            sent += 1
        elif left <= days[0] and not p.remind_5d_sent:
            notify.pass_reminder(db, veh, left, ends_on, link)
            p.remind_5d_sent = True
            sent += 1
    db.flush()
    return sent


def is_pass_candidate(db: Session, vehicle_id: int, now: Optional[datetime] = None) -> bool:
    now = now or utcnow()
    n = int(get_setting(db, "pass_candidate_visits"))
    visits = db.scalar(select(func.count(ParkingSession.id)).where(
        ParkingSession.vehicle_id == vehicle_id, ParkingSession.entry_at >= now - timedelta(days=30))) or 0
    return visits >= n


def pass_dict(db: Session, p: Pass) -> dict:
    pt = db.get(PassType, p.pass_type_id)
    veh = db.get(Vehicle, p.vehicle_id)
    tz = site_tz()
    return {"id": p.id, "vehicle_id": p.vehicle_id, "plate": veh.plate, "display_plate": veh.display_plate,
            "vehicle_class": p.vehicle_class, "pass_type": pt.name, "pass_type_id": pt.id,
            "starts_on": p.starts_at.astimezone(tz).date().isoformat(), "ends_on": p.ends_at.astimezone(tz).date().isoformat(),
            "starts_at": p.starts_at.isoformat(), "ends_at": p.ends_at.isoformat(),
            "amount_paise": p.amount_paise, "status": p.status, "channel": p.channel, "phone": veh.phone}
