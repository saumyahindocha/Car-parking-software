"""Small shared lookups: tariffs, gates, zones, passes, time helpers."""
from __future__ import annotations

from datetime import datetime, time
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Gate, Pass, Tariff, Zone, ZoneAssignment
from .tariff import tariff_for


def site_tz() -> ZoneInfo:
    return ZoneInfo(get_settings().site_timezone)


def local_date(dt: datetime) -> str:
    return dt.astimezone(site_tz()).date().isoformat()


def day_bounds(date_str: str) -> tuple[datetime, datetime]:
    """UTC-aware [start, end) of a local calendar date."""
    from datetime import date, timedelta

    d = date.fromisoformat(date_str)
    tz = site_tz()
    start = datetime.combine(d, time(0), tzinfo=tz)
    return start, start + timedelta(days=1)


def last_valid_day(ends_at: datetime):
    """Passes end at local midnight *after* their last day; this is the last day a customer can use it."""
    from datetime import timedelta

    return (ends_at - timedelta(microseconds=1)).astimezone(site_tz()).date()


def get_tariff(db: Session, vehicle_class: str, at: datetime) -> Tariff:
    rows = db.scalars(select(Tariff).where(Tariff.vehicle_class == vehicle_class,
                                           Tariff.effective_from <= at)).all()
    return tariff_for(rows, vehicle_class, at)  # type: ignore[return-value]


def gate_direction(gate: Gate, at: datetime) -> str:
    """Resolve the gate's direction, honouring its time-of-day schedule (local time)."""
    local = at.astimezone(site_tz())
    hhmm = local.strftime("%H:%M")
    for rule in gate.schedule or []:
        days = rule.get("days")
        if days is not None and local.weekday() not in days:
            continue
        start, end = rule.get("from", "00:00"), rule.get("to", "24:00")
        inside = (start <= hhmm < end) if start <= end else (hhmm >= start or hhmm < end)
        if inside:
            return rule["direction"]
    return gate.direction


def zone_for_gate(db: Session, gate_id: Optional[str]) -> Optional[Zone]:
    if not gate_id:
        return None
    return db.scalars(select(Zone).where(Zone.gate_id == gate_id).order_by(Zone.id)).first()


def worker_on_duty(db: Session, zone_id: Optional[int], at: datetime) -> Optional[int]:
    if zone_id is None:
        return None
    row = db.scalars(select(ZoneAssignment).where(
        ZoneAssignment.zone_id == zone_id, ZoneAssignment.starts_at <= at, ZoneAssignment.ends_at > at,
    ).order_by(ZoneAssignment.starts_at.desc())).first()
    return row.user_id if row else None


def active_pass(db: Session, vehicle_id: int, at: datetime) -> Optional[Pass]:
    return db.scalars(select(Pass).where(
        Pass.vehicle_id == vehicle_id, Pass.status == "ACTIVE",
        Pass.starts_at <= at, Pass.ends_at > at,
    ).order_by(Pass.ends_at.desc())).first()
