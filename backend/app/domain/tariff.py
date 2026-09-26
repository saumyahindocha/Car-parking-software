"""Tariff engine: a pure function from (tariff, entry, exit) to a charge in paise.

Rules (all values come from the versioned tariff row):

* free_minutes: stays up to this length cost nothing (0 by default).
* The stay is split into blocks of `block_minutes` (default 12 h). Each full block costs
  `block_cap_paise`. Within a block: `first_slab_paise` covers the first
  `first_slab_minutes`; each *started* additional hour costs `per_hour_paise`; the block
  total never exceeds `block_cap_paise`.
* Grace: `grace_minutes` is allowed past every slab boundary before the next unit is
  charged (so with a 2 h slab and 10 min grace, 2 h 10 min costs the first slab only;
  2 h 11 min adds one hour). A remainder of <= grace after full blocks is free.
* daily_cap_paise (optional): total charge for each started 24 h period never exceeds it.
* overnight_paise: added once for every local `overnight_cutoff_hour` crossed during the
  stay (0 = disabled by default).

The tariff in force at *entry* applies to the whole stay (see `tariff_for`).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Iterable, Optional, Protocol
from zoneinfo import ZoneInfo


class TariffLike(Protocol):
    vehicle_class: str
    effective_from: datetime
    first_slab_minutes: int
    first_slab_paise: int
    per_hour_paise: int
    grace_minutes: int
    block_minutes: int
    block_cap_paise: Optional[int]
    daily_cap_paise: Optional[int]
    overnight_paise: int
    overnight_cutoff_hour: int
    free_minutes: int


@dataclass
class TariffSpec:
    """Plain value object satisfying TariffLike (for tests, simulation and API previews)."""

    vehicle_class: str = "BIKE"
    effective_from: datetime = datetime(2000, 1, 1, tzinfo=ZoneInfo("UTC"))
    first_slab_minutes: int = 120
    first_slab_paise: int = 1000
    per_hour_paise: int = 500
    grace_minutes: int = 10
    block_minutes: int = 720
    block_cap_paise: Optional[int] = 3000
    daily_cap_paise: Optional[int] = None
    overnight_paise: int = 0
    overnight_cutoff_hour: int = 0
    free_minutes: int = 0
    version: int = 1


def _block_charge(t: TariffLike, minutes: float) -> int:
    """Charge for `minutes` (> 0) within a single block."""
    over = minutes - t.first_slab_minutes - t.grace_minutes
    extra_hours = max(0, math.ceil(over / 60)) if over > 0 else 0
    charge = t.first_slab_paise + extra_hours * t.per_hour_paise
    if t.block_cap_paise is not None:
        charge = min(charge, t.block_cap_paise)
    return charge


def _span_charge(t: TariffLike, minutes: float) -> int:
    """Charge for a span made of full blocks plus a remainder."""
    if minutes <= 0:
        return 0
    block = t.block_minutes if t.block_minutes and t.block_minutes > 0 else None
    if block is None:
        return _block_charge(t, minutes)
    full, rem = divmod(minutes, block)
    full = int(full)
    cap = t.block_cap_paise if t.block_cap_paise is not None else _block_charge(t, block)
    total = full * cap
    if rem > 0 and not (full > 0 and rem <= t.grace_minutes):
        total += _block_charge(t, rem)
    return total


def count_overnights(entry: datetime, exit_: datetime, cutoff_hour: int, tz: ZoneInfo) -> int:
    """Number of local `cutoff_hour:00` instants in (entry, exit]."""
    le, lx = entry.astimezone(tz), exit_.astimezone(tz)
    n = 0
    d = le.date()
    while d <= lx.date():
        instant = datetime.combine(d, time(cutoff_hour), tzinfo=tz)
        if le < instant <= lx:
            n += 1
        d += timedelta(days=1)
    return n


def calculate_charge(
    vehicle_class: str,
    entry_time: datetime,
    exit_time: datetime,
    tariff_version: TariffLike,
    tz: str | ZoneInfo = "Asia/Kolkata",
) -> int:
    """Charge in paise for a stay. Pure: depends only on its arguments."""
    t = tariff_version
    if t.vehicle_class != vehicle_class:
        raise ValueError(f"tariff is for {t.vehicle_class}, not {vehicle_class}")
    if entry_time.tzinfo is None or exit_time.tzinfo is None:
        raise ValueError("entry_time and exit_time must be timezone-aware")
    zone = tz if isinstance(tz, ZoneInfo) else ZoneInfo(tz)
    minutes = (exit_time - entry_time).total_seconds() / 60.0
    if minutes < 0:
        raise ValueError("exit before entry")
    if t.free_minutes > 0 and minutes <= t.free_minutes:
        return 0
    minutes = max(minutes, 1e-9)

    if t.daily_cap_paise is not None:
        day = 1440
        full_days, rem = divmod(minutes, day)
        per_day = min(_span_charge(t, day), t.daily_cap_paise)
        total = int(full_days) * per_day
        if rem > 0 and not (full_days > 0 and rem <= t.grace_minutes):
            total += min(_span_charge(t, rem), t.daily_cap_paise)
    else:
        total = _span_charge(t, minutes)

    if t.overnight_paise:
        total += t.overnight_paise * count_overnights(entry_time, exit_time, t.overnight_cutoff_hour, zone)
    return int(total)


def tariff_for(tariffs: Iterable[TariffLike], vehicle_class: str, at: datetime) -> TariffLike:
    """The tariff version in force at `at` (latest effective_from <= at) for the class."""
    best = None
    for t in tariffs:
        if t.vehicle_class != vehicle_class or t.effective_from > at:
            continue
        if best is None or t.effective_from > best.effective_from:
            best = t
    if best is None:
        raise LookupError(f"no tariff for {vehicle_class} at {at.isoformat()}")
    return best
