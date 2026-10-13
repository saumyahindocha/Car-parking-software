"""Background jobs on the edge server (single-process scheduler thread).

* deliver queued SMS/WhatsApp                         every 10 s
* flag unpaid sessions (> N min, revenue protection)   every 60 s
* watchdog: camera stall > 60 s, internet down > 5 min every 30 s
* UPI reconciliation (confirms offline claims)         every 10 min when online
* stale offline claims -> review alerts                hourly
* pass expiry and reminders                            every 30 min
* retention purge of images                            daily
* cloud relay sync                                     every 15 s (if PARK_RELAY_URL set)
"""
from __future__ import annotations

import logging
import socket
import threading
import time
from datetime import timedelta
from typing import Callable

from sqlalchemy import select

from . import events
from .adapters.gateway import GatewayUnavailable, get_gateway
from .config import get_settings
from .db import session_scope, utcnow
from .domain import notify, passes, payments, privacy, reports
from .domain.lookup import local_date
from .domain.settings import get_setting, set_setting
from .models import Alert, Device, Setting

log = logging.getLogger(__name__)


def job_messages(db):
    notify.deliver_pending(db)


def job_flag_unpaid(db):
    ids = reports.flag_unpaid_sessions(db)
    if ids:
        events.emit(db, "review.new", {"reason": "UNPAID_30MIN", "session_ids": ids[:100], "count": len(ids)})


def _internet_ok() -> bool:
    for host in ("1.1.1.1", "8.8.8.8"):
        try:
            socket.create_connection((host, 53), timeout=2).close()
            return True
        except OSError:
            continue
    return False


def job_watchdog(db):
    now = utcnow()
    stall = int(get_setting(db, "camera_stall_seconds"))
    for d in db.scalars(select(Device).where(Device.kind == "CAMERA")).all():
        last_frame = (d.metrics or {}).get("last_frame_ts_ms")
        age = (now.timestamp() - last_frame / 1000) if last_frame else ((now - d.last_seen).total_seconds() if d.last_seen else None)
        if age is not None and age > stall and not d.stalled_alerted:
            d.stalled_alerted = True
            a = Alert(kind="CAMERA_STALL", severity="CRIT", gate_id=d.gate_id, message=f"Camera {d.id} stalled for {int(age)} s",
                      data={"device_id": d.id})
            db.add(a)
            db.flush()
            events.emit(db, "alert", {"id": a.id, "kind": a.kind, "gate_id": d.gate_id, "message": a.message})
        elif age is not None and age <= stall and d.stalled_alerted:
            d.stalled_alerted = False
    online = _internet_ok()
    gw_online = get_gateway().healthy() if online else False
    row = db.get(Setting, "_internet_status")
    state = dict(row.value) if row else {"online": True, "since": now.isoformat(), "alerted": False}
    if state.get("online") != online:
        state = {"online": online, "since": now.isoformat(), "alerted": False}
    state["gateway_online"] = gw_online
    state["checked_at"] = now.isoformat()
    from datetime import datetime

    down_for = (now - datetime.fromisoformat(state["since"])).total_seconds()
    if not online and down_for > int(get_setting(db, "internet_down_alert_seconds")) and not state.get("alerted"):
        state["alerted"] = True
        a = Alert(kind="INTERNET_DOWN", severity="CRIT", message=f"Internet down for {int(down_for // 60)} min — UPI on offline path")
        db.add(a)
        db.flush()
        events.emit(db, "alert", {"id": a.id, "kind": a.kind, "message": a.message})
    set_setting(db, "_internet_status", state)
    events.emit(db, "device.health", {"internet": state})


def job_upi_recon(db):
    gw = get_gateway()
    if not gw.healthy():
        return
    today = utcnow()
    try:
        for d in (today - timedelta(days=1), today):
            lines = gw.fetch_settlements(d.date())
            payments.reconcile_upi(db, lines, day=local_date(d))
    except GatewayUnavailable:
        return


def job_stale_claims(db):
    recent = db.scalars(select(Alert).where(Alert.kind == "OFFLINE_CLAIM_STALE",
                                            Alert.created_at > utcnow() - timedelta(days=3))).all()
    alerted = {a.data.get("payment_id") for a in recent}
    for p in payments.stale_offline_claims(db):
        if p.id in alerted:
            continue
        a = Alert(kind="OFFLINE_CLAIM_STALE", severity="WARN", vehicle_id=p.vehicle_id, session_id=p.session_id,
                  message=f"Offline UPI claim {p.txn_ref} (₹{p.amount_paise / 100:.0f}) unconfirmed for 24 h",
                  data={"payment_id": p.id})
        db.add(a)
        db.flush()
        events.emit(db, "review.new", {"payment_id": p.id, "reason": "OFFLINE_CLAIM_STALE"})


def job_passes(db):
    passes.expire_passes(db)
    passes.send_reminders(db)


def job_retention(db):
    res = privacy.purge_images(db)
    log.info("retention purge: %s", res)


def job_relay(db):
    s = get_settings()
    if not s.relay_url:
        return
    from .relay_sync import RelayClient, sync_once

    sync_once(db, RelayClient(s.relay_url, s.relay_api_key))


JOBS: list[tuple[str, int, Callable]] = [
    ("messages", 10, job_messages),
    ("flag_unpaid", 60, job_flag_unpaid),
    ("watchdog", 30, job_watchdog),
    ("upi_recon", 600, job_upi_recon),
    ("stale_claims", 3600, job_stale_claims),
    ("passes", 1800, job_passes),
    ("retention", 86400, job_retention),
    ("relay", 15, job_relay),
]


class Scheduler(threading.Thread):
    def __init__(self) -> None:
        super().__init__(name="jobs", daemon=True)
        self.stop_event = threading.Event()
        self.next_run = {name: time.monotonic() + 5 for name, _, _ in JOBS}

    def run(self) -> None:
        while not self.stop_event.is_set():
            now = time.monotonic()
            for name, every, fn in JOBS:
                if now >= self.next_run[name]:
                    self.next_run[name] = now + every
                    try:
                        with session_scope() as db:
                            fn(db)
                    except Exception:
                        log.exception("job %s failed", name)
            self.stop_event.wait(1.0)

    def stop(self) -> None:
        self.stop_event.set()
