"""Reports for the dashboard (spec 5A worker comparison, 11 reports, 12 revenue protection)."""
from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from ..db import utcnow
from ..models import (
    AnprEvent,
    CashHandover,
    EventStatus,
    LedgerEntry,
    LedgerKind,
    Override,
    ParkingSession,
    Pass,
    PaymentDispute,
    Payment,
    PayMode,
    PayStatus,
    PlateCorrection,
    Receipt,
    SessionStatus,
    Shift,
    User,
    Vehicle,
    Zone,
)
from .lookup import day_bounds, site_tz
from .settings import get_setting

PAID = (PayStatus.CONFIRMED, PayStatus.CLAIMED_OFFLINE)


def _range(start_date: str, end_date: Optional[str]) -> tuple[datetime, datetime]:
    s, _ = day_bounds(start_date)
    _, e = day_bounds(end_date or start_date)
    return s, e


# ------------------------------------------------------------------ live
def occupancy(db: Session) -> dict:
    rows = db.execute(select(ParkingSession.vehicle_class, ParkingSession.status, func.count(ParkingSession.id))
                      .where(ParkingSession.status.in_(SessionStatus.ACTIVE))
                      .group_by(ParkingSession.vehicle_class, ParkingSession.status)).all()
    out: dict = defaultdict(lambda: {"total": 0, "OPEN": 0, "PREPAID": 0, "PASS": 0})
    for vc, st, n in rows:
        out[vc][st] += n
        out[vc]["total"] += n
    return dict(out)


def movements_per_hour(db: Session, date: str) -> list[dict]:
    start, end = day_bounds(date)
    tz = site_tz()
    counts: Counter = Counter()
    for gate, direction, ts in db.execute(select(AnprEvent.gate_id, AnprEvent.direction, AnprEvent.ts).where(
            AnprEvent.ts >= start, AnprEvent.ts < end,
            AnprEvent.status.notin_([EventStatus.DUPLICATE, EventStatus.DISCARDED]))).all():
        counts[(gate, direction, ts.astimezone(tz).hour)] += 1
    return [{"gate_id": g, "direction": d, "hour": h, "count": n} for (g, d, h), n in sorted(counts.items())]


def unpaid_flagged_sessions(db: Session) -> list[ParkingSession]:
    return list(db.scalars(select(ParkingSession).where(ParkingSession.status == SessionStatus.OPEN,
                                                        ParkingSession.unpaid_flagged.is_(True))
                           .order_by(ParkingSession.entry_at)).all())


def flag_unpaid_sessions(db: Session, now: Optional[datetime] = None) -> list[int]:
    """Revenue protection: OPEN (no payment) non-pass sessions older than N minutes are flagged."""
    now = now or utcnow()
    mins = int(get_setting(db, "unpaid_flag_minutes"))
    rows = db.scalars(select(ParkingSession).where(ParkingSession.status == SessionStatus.OPEN,
                                                   ParkingSession.unpaid_flagged.is_(False),
                                                   ParkingSession.entry_at < now - timedelta(minutes=mins))).all()
    for s in rows:
        s.unpaid_flagged = True
    db.flush()
    return [s.id for s in rows]


# ------------------------------------------------------------------ revenue
def daily_revenue(db: Session, start_date: str, end_date: Optional[str] = None) -> list[dict]:
    start, end = _range(start_date, end_date)
    tz = site_tz()
    days: dict = defaultdict(lambda: {"walkin_upi_paise": 0, "walkin_cash_paise": 0, "pass_upi_paise": 0,
                                      "pass_cash_paise": 0, "claimed_offline_paise": 0, "charges_paise": 0,
                                      "sessions": 0, "pass_sessions": 0, "payments": 0})
    for mode, purpose, status, amt, ts in db.execute(select(Payment.mode, Payment.purpose, Payment.status,
                                                            Payment.amount_paise, Payment.created_at).where(
            Payment.created_at >= start, Payment.created_at < end, Payment.status.in_(PAID))).all():
        d = days[ts.astimezone(tz).date().isoformat()]
        if status == PayStatus.CLAIMED_OFFLINE:
            d["claimed_offline_paise"] += amt
            continue
        key = ("pass" if purpose == "PASS" else "walkin") + ("_upi_paise" if mode == PayMode.UPI else "_cash_paise")
        d[key] += amt
        d["payments"] += 1
    for amt, ts in db.execute(select(LedgerEntry.amount_paise, LedgerEntry.created_at).where(
            LedgerEntry.kind == LedgerKind.CHARGE, LedgerEntry.created_at >= start, LedgerEntry.created_at < end)).all():
        days[ts.astimezone(tz).date().isoformat()]["charges_paise"] += amt
    for (ts,) in db.execute(select(ParkingSession.entry_at).where(
            ParkingSession.entry_at >= start, ParkingSession.entry_at < end)).all():
        days[ts.astimezone(tz).date().isoformat()]["sessions"] += 1
    for (ts,) in db.execute(select(ParkingSession.entry_at).where(
            ParkingSession.entry_at >= start, ParkingSession.entry_at < end, ParkingSession.pass_id.is_not(None))).all():
        days[ts.astimezone(tz).date().isoformat()]["pass_sessions"] += 1
    out = []
    for day in sorted(days):
        d = days[day]
        d["total_paise"] = d["walkin_upi_paise"] + d["walkin_cash_paise"] + d["pass_upi_paise"] + d["pass_cash_paise"]
        out.append({"date": day, **d})
    return out


def sessions_without_payment(db: Session, start_date: str, end_date: Optional[str] = None) -> list[dict]:
    start, end = _range(start_date, end_date)
    paid_sessions = select(Payment.session_id).where(Payment.status.in_(PAID), Payment.session_id.is_not(None))
    rows = db.execute(select(ParkingSession, Vehicle).join(Vehicle, ParkingSession.vehicle_id == Vehicle.id).where(
        ParkingSession.entry_at >= start, ParkingSession.entry_at < end,
        ParkingSession.pass_id.is_(None), ParkingSession.id.notin_(paid_sessions),
        ParkingSession.status.notin_([SessionStatus.PASS]))).all()
    return [{"session_id": s.id, "plate": v.plate, "entry_at": s.entry_at.isoformat(),
             "exit_at": s.exit_at.isoformat() if s.exit_at else None, "status": s.status, "zone_id": s.zone_id,
             "charge_paise": s.charge_paise, "balance_paise": v.balance_paise} for s, v in rows]


# ------------------------------------------------------------------ worker comparison (5A)
def worker_comparison(db: Session, start_date: str, end_date: Optional[str] = None) -> dict:
    start, end = _range(start_date, end_date)
    shifts = db.scalars(select(Shift).where(Shift.opened_at < end, or_(Shift.closed_at.is_(None), Shift.closed_at > start))).all()
    now = utcnow()
    rows = []
    for sh in shifts:
        u = db.get(User, sh.user_id)
        s_end = min(sh.closed_at or now, end)
        s_start = max(sh.opened_at, start)
        pays = db.execute(select(Payment.mode, Payment.amount_paise, Payment.receipt_id).where(
            Payment.shift_id == sh.id, Payment.status.in_(PAID))).all()
        upi = sum(a for m, a, _ in pays if m == PayMode.UPI)
        cash_amt = sum(a for m, a, _ in pays if m == PayMode.CASH)
        cash_n = sum(1 for m, _, _ in pays if m == PayMode.CASH)
        rec_ids = [r for m, _, r in pays if m == PayMode.CASH and r]
        to_phone = shown = 0
        if rec_ids:
            for ch, st in db.execute(select(Receipt.channel, Receipt.delivery_status).where(Receipt.id.in_(rec_ids))).all():
                if ch in ("SMS", "WHATSAPP"):
                    to_phone += 1
                elif st == "SHOWN":
                    shown += 1
        zone_sessions = unpaid = 0
        if sh.zone_id is not None:
            paid_sessions = select(Payment.session_id).where(Payment.status.in_(PAID), Payment.session_id.is_not(None))
            base = and_(ParkingSession.zone_id == sh.zone_id, ParkingSession.entry_at >= s_start,
                        ParkingSession.entry_at < s_end, ParkingSession.pass_id.is_(None))
            zone_sessions = db.scalar(select(func.count(ParkingSession.id)).where(base)) or 0
            unpaid = db.scalar(select(func.count(ParkingSession.id)).where(base, ParkingSession.id.notin_(paid_sessions))) or 0
        disputes = Counter(st for (st,) in db.execute(select(PaymentDispute.status).where(
            PaymentDispute.worker_id == sh.user_id, PaymentDispute.created_at >= s_start,
            PaymentDispute.created_at < s_end + timedelta(hours=12))).all())
        hv = db.execute(select(func.coalesce(func.sum(CashHandover.variance_paise), 0), func.count(CashHandover.id)).where(
            CashHandover.shift_id == sh.id, CashHandover.status == "CONFIRMED")).one()
        breaches = db.scalar(select(func.count(Payment.id)).where(Payment.shift_id == sh.id, Payment.limit_breach.is_(True))) or 0
        total = upi + cash_amt
        rows.append({
            "shift_id": sh.id, "user_id": sh.user_id, "name": u.name if u else None, "zone_id": sh.zone_id,
            "opened_at": sh.opened_at.isoformat(), "closed_at": sh.closed_at.isoformat() if sh.closed_at else None,
            "collections": len(pays), "upi_paise": upi, "cash_paise": cash_amt, "cash_count": cash_n,
            "cash_share": (cash_amt / total) if total else 0.0,
            "zone_sessions": zone_sessions, "unpaid_in_zone": unpaid,
            "unpaid_rate": (unpaid / zone_sessions) if zone_sessions else 0.0,
            "disputes_total": sum(disputes.values()), "disputes_upheld": disputes.get("UPHELD", 0),
            "disputes_unresolved": disputes.get("UNRESOLVED", 0) + disputes.get("OPEN", 0),
            "disputes_rejected": disputes.get("REJECTED", 0),
            "handover_variance_paise": int(hv[0]), "handovers": int(hv[1]), "shift_variance_paise": sh.variance_paise or 0,
            "cash_receipts_to_phone": to_phone, "cash_receipts_shown": shown,
            "cash_receipt_phone_share": (to_phone / cash_n) if cash_n else None,
            "limit_breaches": breaches, "flags": [],
        })
    _flag_outliers(rows)
    return {"rows": rows, "averages": _averages(rows)}


def _averages(rows: list[dict]) -> dict:
    act = [r for r in rows if r["collections"] or r["zone_sessions"]]
    if not act:
        return {}
    return {"cash_share": statistics.mean(r["cash_share"] for r in act),
            "unpaid_rate": statistics.mean(r["unpaid_rate"] for r in act if r["zone_sessions"]) if any(r["zone_sessions"] for r in act) else 0.0,
            "disputes": statistics.mean(r["disputes_total"] for r in act)}


def _flag_outliers(rows: list[dict]) -> None:
    """Automatic outlier flags for the worker comparison report.

    Unpaid-in-zone is tested against the pooled rate of the *other* shifts (binomial z-score), so
    small shifts are not flagged on noise. The spec's example rule (unpaid above average combined
    with below-average cash) is applied on top of a z-score > 1. Disputes are the strongest signal.
    """
    act = [r for r in rows if r["zone_sessions"] >= 20]
    avg_share = statistics.mean(r["cash_share"] for r in act) if act else 0.0
    avg_unpaid = statistics.mean(r["unpaid_rate"] for r in act) if act else 0.0
    for r in act:
        others = [x for x in act if x is not r]
        n_o = sum(x["zone_sessions"] for x in others)
        if not n_o:
            continue
        p = sum(x["unpaid_in_zone"] for x in others) / n_o
        n = r["zone_sessions"]
        sd = (n * p * (1 - p)) ** 0.5 or 1.0
        z = (r["unpaid_in_zone"] - n * p) / sd
        r["unpaid_z"] = round(z, 2)
        if z > 2.0 and r["unpaid_in_zone"] - n * p >= 3:
            r["flags"].append("HIGH_UNPAID_IN_ZONE")
        if z > 1.0 and r["unpaid_rate"] > avg_unpaid and r["cash_share"] < avg_share:
            r["flags"].append("HIGH_UNPAID_LOW_CASH")
        if avg_share and r["cash_count"] >= 10 and r["cash_share"] < 0.6 * avg_share:
            r["flags"].append("LOW_CASH_SHARE")
    for r in rows:
        repeated = r["disputes_upheld"] + r["disputes_unresolved"] >= 3
        if repeated:
            r["flags"].append("REPEATED_DISPUTES")
            if r["unpaid_rate"] > avg_unpaid:
                r["flags"].append("SUSPECT_UNRECORDED_CASH")
        if r["handover_variance_paise"] < 0 or r["shift_variance_paise"] < 0:
            r["flags"].append("CASH_SHORT")
        if r["limit_breaches"]:
            r["flags"].append("LIMIT_BREACH")
        if r["cash_count"] >= 10 and (r["cash_receipt_phone_share"] or 0) < 0.2:
            r["flags"].append("LOW_RECEIPT_TO_PHONE")


def disputes_by_worker(db: Session, start_date: str, end_date: Optional[str] = None) -> list[dict]:
    start, end = _range(start_date, end_date)
    agg: dict = defaultdict(Counter)
    for wid, st in db.execute(select(PaymentDispute.worker_id, PaymentDispute.status).where(
            PaymentDispute.created_at >= start, PaymentDispute.created_at < end)).all():
        agg[wid][st] += 1
    out = []
    for wid, c in agg.items():
        u = db.get(User, wid) if wid else None
        out.append({"worker_id": wid, "name": u.name if u else "(unattributed)", **{k: c.get(k, 0) for k in
                    ("OPEN", "UPHELD", "REJECTED", "UNRESOLVED")}, "total": sum(c.values()),
                    "repeat_flag": c.get("UPHELD", 0) + c.get("UNRESOLVED", 0) + c.get("OPEN", 0) >= 3})
    return sorted(out, key=lambda r: -r["total"])


def collections_per_worker(db: Session, start_date: str, end_date: Optional[str] = None) -> list[dict]:
    return [{k: r[k] for k in ("shift_id", "name", "zone_id", "opened_at", "closed_at", "collections", "upi_paise",
                              "cash_paise", "cash_share", "handover_variance_paise")}
            for r in worker_comparison(db, start_date, end_date)["rows"]]


def overrides_report(db: Session, start_date: str, end_date: Optional[str] = None) -> list[dict]:
    start, end = _range(start_date, end_date)
    rows = db.scalars(select(Override).where(Override.created_at >= start, Override.created_at < end)
                      .order_by(Override.created_at)).all()
    return [{"id": o.id, "kind": o.kind, "payment_id": o.payment_id, "session_id": o.session_id, "vehicle_id": o.vehicle_id,
             "original_paise": o.original_paise, "new_paise": o.new_paise, "reason": o.reason,
             "requested_by": o.requested_by, "approved_by": o.approved_by, "created_at": o.created_at.isoformat()}
            for o in rows]


# ------------------------------------------------------------------ ANPR accuracy
def anpr_accuracy(db: Session, start_date: str, end_date: Optional[str] = None) -> list[dict]:
    start, end = _range(start_date, end_date)
    per: dict = defaultdict(Counter)
    evs = db.execute(select(AnprEvent.id, AnprEvent.camera_ids, AnprEvent.status, AnprEvent.match_type,
                            AnprEvent.review_reason).where(AnprEvent.ts >= start, AnprEvent.ts < end,
                                                           AnprEvent.status != EventStatus.DUPLICATE)).all()
    corrected = {eid for (eid,) in db.execute(select(PlateCorrection.event_id).where(
        PlateCorrection.created_at >= start - timedelta(days=1), PlateCorrection.source.in_(["WORKER", "REVIEW"]))).all()}
    for eid, cams, status, match, reason in evs:
        for cam in (cams or ["?"]):
            c = per[cam]
            c["events"] += 1
            if status == EventStatus.UNREAD or reason == "UNREAD":
                c["unread"] += 1
            if match == "APPROX":
                c["approx"] += 1
            if eid in corrected or match == "MANUAL" or status in (EventStatus.REVIEW, EventStatus.RESOLVED):
                c["manual"] += 1
    out = []
    for cam, c in sorted(per.items()):
        n = c["events"] or 1
        out.append({"camera_id": cam, "events": c["events"], "read_rate": 1 - c["unread"] / n,
                    "approx_rate": c["approx"] / n, "manual_rate": c["manual"] / n,
                    "exact_auto_rate": 1 - (c["unread"] + c["approx"] + c["manual"]) / n})
    return out


def peak_hours(db: Session, start_date: str, end_date: Optional[str] = None) -> list[dict]:
    start, end = _range(start_date, end_date)
    tz = site_tz()
    c: Counter = Counter()
    for direction, ts in db.execute(select(AnprEvent.direction, AnprEvent.ts).where(
            AnprEvent.ts >= start, AnprEvent.ts < end, AnprEvent.status != EventStatus.DUPLICATE)).all():
        c[(ts.astimezone(tz).hour, direction)] += 1
    return [{"hour": h, "IN": c.get((h, "IN"), 0), "OUT": c.get((h, "OUT"), 0)} for h in range(24)]


# ------------------------------------------------------------------ passes & defaulters
def pass_report(db: Session, start_date: str, end_date: Optional[str] = None) -> dict:
    start, end = _range(start_date, end_date)
    now = utcnow()
    active = db.scalar(select(func.count(Pass.id)).where(Pass.status == "ACTIVE", Pass.starts_at <= now, Pass.ends_at > now)) or 0
    expiring = db.scalars(select(Pass).where(Pass.status == "ACTIVE", Pass.ends_at > now,
                                             Pass.ends_at <= now + timedelta(days=7))).all()
    sold = db.execute(select(func.count(Pass.id), func.coalesce(func.sum(Pass.amount_paise), 0)).where(
        Pass.status.in_(["ACTIVE", "EXPIRED"]), Pass.created_at >= start, Pass.created_at < end)).one()
    total_sessions = db.scalar(select(func.count(ParkingSession.id)).where(
        ParkingSession.entry_at >= start, ParkingSession.entry_at < end)) or 0
    pass_sessions = db.scalar(select(func.count(ParkingSession.id)).where(
        ParkingSession.entry_at >= start, ParkingSession.entry_at < end, ParkingSession.pass_id.is_not(None))) or 0
    from .passes import pass_dict

    return {"active": active, "expiring_this_week": [pass_dict(db, p) for p in expiring],
            "sold_count": int(sold[0]), "revenue_paise": int(sold[1]),
            "traffic_share_on_pass": (pass_sessions / total_sessions) if total_sessions else 0.0,
            "pass_sessions": pass_sessions, "total_sessions": total_sessions}


def defaulters(db: Session, threshold_paise: Optional[int] = None, one_time_days: int = 7) -> dict:
    thr = threshold_paise if threshold_paise is not None else 0
    now = utcnow()
    visits = dict(db.execute(select(ParkingSession.vehicle_id, func.count(ParkingSession.id))
                             .group_by(ParkingSession.vehicle_id)).all())
    regular, one_time = [], []
    for v in db.scalars(select(Vehicle).where(Vehicle.balance_paise > thr).order_by(Vehicle.balance_paise.desc())).all():
        row = {"vehicle_id": v.id, "plate": v.plate, "display_plate": v.display_plate, "balance_paise": v.balance_paise,
               "last_seen": v.last_seen.isoformat(), "phone": v.phone, "visits": visits.get(v.id, 0)}
        if visits.get(v.id, 0) <= 1 and v.last_seen < now - timedelta(days=one_time_days):
            one_time.append(row)
        else:
            regular.append(row)
    return {"defaulters": regular, "unrecovered_one_time": one_time,
            "defaulters_total_paise": sum(r["balance_paise"] for r in regular),
            "unrecovered_total_paise": sum(r["balance_paise"] for r in one_time)}


def review_queue_counts(db: Session) -> dict:
    return {
        "unread": db.scalar(select(func.count(AnprEvent.id)).where(AnprEvent.status == EventStatus.UNREAD)) or 0,
        "review": db.scalar(select(func.count(AnprEvent.id)).where(AnprEvent.status == EventStatus.REVIEW)) or 0,
        "orphans": db.scalar(select(func.count(ParkingSession.id)).where(ParkingSession.status.in_(
            [SessionStatus.ORPHAN_ENTRY, SessionStatus.ORPHAN_EXIT]), ParkingSession.pass_id.is_(None))) or 0,
        "offline_claims": db.scalar(select(func.count(Payment.id)).where(Payment.status == PayStatus.CLAIMED_OFFLINE)) or 0,
        "disputes": db.scalar(select(func.count(PaymentDispute.id)).where(PaymentDispute.status == "OPEN")) or 0,
    }


def zone_names(db: Session) -> dict[int, str]:
    return {z.id: z.name for z in db.scalars(select(Zone)).all()}
