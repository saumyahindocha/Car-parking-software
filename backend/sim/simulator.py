"""Synthetic full-day simulator (spec section 15).

Replays a realistic commuter day through the real backend services on a virtual clock:

* ~3,000 bikes, morning entry peak / evening exit peak, both gates bidirectional
* 40 % monthly-pass holders (zero interaction)
* frequent side-by-side pairs (same gate, same instant)
* 8 % unreadable (UNREAD) or misread (confusion / one-character) plates
* 10 % of walk-ins underpay (choose a shorter duration than they stay)
* 2 % one-time visitors who underpay and never return
* 25 % of walk-in payments in cash, the rest UPI (plus a few self-pay via the cloud relay)
* one dishonest worker pockets 5 % of their cash without recording it
* a 20-minute internet outage in the morning peak (UPI offline path, reconciled afterwards)
* missed collections, exit alerts, guard disputes, supervisor review, handovers, bank deposit

`run_simulation()` returns a `SimResult` with the system's reports and the simulator's own
ground truth so tests (tests/test_simulation.py) can assert revenue, balances, pass handling,
cash reconciliation, review queues and worker accountability.

CLI:  python -m sim.simulator [--vehicles 3000] [--seed 7] [--db sqlite:///sim.db]
"""
from __future__ import annotations

import argparse
import heapq
import itertools
import random
import time as _time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, func, select
from sqlalchemy.pool import StaticPool

from app import domain  # noqa: F401  (hooks)
from app import relay_sync
from app.adapters.gateway import MockGateway, set_gateway
from app.adapters.messaging import Messenger, NoOpSender, set_messenger
from app.db import Base, SessionLocal, set_clock, set_engine
from app.domain import cash, disputes, ledger, payments, reports
from app.domain import sessions as sess_svc
from app.domain.lookup import get_tariff
from app.domain.payments import CashLimitReached, quote_session
from app.domain.settings import set_setting
from app.domain.tariff import calculate_charge
from app.models import (Alert, AnprEvent, EventStatus, Gate, LedgerEntry, LedgerKind, ParkingSession, PassType, Payment,
                        PayMode, PayStatus, Role, SessionStatus, User, Vehicle, Zone, ZoneAssignment)
from app.seed import seed_reference, seed_users

IST = ZoneInfo("Asia/Kolkata")
STATES = ["MH"] * 8 + ["KA", "GJ", "DL", "TS", "MP"]
LETTERS = "ABCDEFHJKLMNPRTUVWXY"
CONFUSE = {"B": "8", "8": "B", "0": "O", "O": "0", "5": "S", "S": "5", "2": "Z", "Z": "2", "1": "I", "6": "G", "G": "6"}


# ------------------------------------------------------------------ virtual clock
class Clock:
    def __init__(self, start: datetime):
        self.now = start

    def __call__(self) -> datetime:
        return self.now


# ------------------------------------------------------------------ population
@dataclass
class Rider:
    plate: str
    kind: str  # PASS | WALKIN | ONETIME
    phone: Optional[str] = None
    entry_at: Optional[datetime] = None
    exit_at: Optional[datetime] = None
    entry_gate: str = "G1"
    exit_gate: str = "G2"
    pay_mode: str = "UPI"  # UPI | CASH | SELF | NONE
    underpay: bool = False
    entry_read: str = "OK"  # OK | UNREAD | MISREAD
    exit_read: str = "OK"
    entry_misread: Optional[str] = None
    exit_misread: Optional[str] = None
    pocketed: bool = False  # dishonest worker took cash, recorded nothing
    paid_paise: int = 0
    session_id: Optional[int] = None
    fake_offline_claim: bool = False
    prior_dues_paise: int = 0


def make_plate(rng: random.Random, used: set[str]) -> str:
    while True:
        p = (rng.choice(STATES) + f"{rng.randint(1, 50):02d}" + rng.choice(LETTERS) + rng.choice(LETTERS)
             + f"{rng.randint(1, 9999):04d}")
        if p not in used:
            used.add(p)
            return p


def misread(rng: random.Random, plate: str) -> str:
    """One confusion substitution (common) or one random character change in the numeric tail."""
    chars = list(plate)
    if rng.random() < 0.6:
        idx = [i for i, c in enumerate(chars) if c in CONFUSE]
        if idx:
            i = rng.choice(idx)
            chars[i] = CONFUSE[chars[i]]
            return "".join(chars)
    i = rng.randint(len(chars) - 4, len(chars) - 1)
    chars[i] = rng.choice([d for d in "0123456789" if d != chars[i]])
    return "".join(chars)


def sample_entry(rng: random.Random, day: date) -> datetime:
    r = rng.random()
    if r < 0.72:  # morning commuter peak ~ N(8:40, 45 min)
        m = rng.gauss(8 * 60 + 40, 45)
    elif r < 0.9:  # midday
        m = rng.uniform(10 * 60 + 30, 16 * 60)
    else:  # evening arrivals
        m = rng.gauss(17 * 60 + 30, 50)
    m = min(max(m, 6 * 60), 21 * 60)
    return datetime.combine(day, time(0), tzinfo=IST) + timedelta(minutes=m)


def sample_stay(rng: random.Random, entry: datetime) -> timedelta:
    local = entry.astimezone(IST)
    if local.hour < 10 and rng.random() < 0.85:  # commuters: back in the evening peak ~ 18:45
        back = datetime.combine(local.date(), time(0), tzinfo=IST) + timedelta(minutes=rng.gauss(18 * 60 + 45, 50))
        if back > entry + timedelta(hours=2):
            return back - entry
    return timedelta(minutes=max(20, rng.gauss(150, 80)))


def duration_for(stay: timedelta, buttons: list[int]) -> int:
    mins = stay.total_seconds() / 60
    for b in buttons:
        if mins <= b:
            return b
    return buttons[-1]


# ------------------------------------------------------------------ results
@dataclass
class SimResult:
    day: str
    riders: list[Rider]
    counts: Counter
    expected_payments_paise: int
    pocketed_paise: int
    dishonest_worker: str
    worker_report: dict
    cash_recon: dict
    upi_recon: dict
    review_counts: dict
    revenue: list
    defaulters: dict
    pass_report: dict
    ledger_mismatches: list
    disputes: list
    anpr_accuracy: list
    elapsed_s: float
    extra: dict = field(default_factory=dict)


# ------------------------------------------------------------------ simulator
class Simulator:
    def __init__(self, n_vehicles: int = 3000, seed: int = 7, day: date = date(2026, 3, 11),
                 db_url: Optional[str] = None, log: Callable[[str], None] = print):
        self.rng = random.Random(seed)
        self.n = n_vehicles
        self.day = day
        self.log = log
        self.clock = Clock(datetime.combine(day - timedelta(days=1), time(9), tzinfo=IST))
        set_clock(self.clock)
        if db_url:
            eng = create_engine(db_url)
        else:
            eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.drop_all(eng)
        Base.metadata.create_all(eng)
        set_engine(eng)
        self.gw = MockGateway()
        set_gateway(self.gw)
        set_messenger(Messenger(sms=NoOpSender("SMS"), whatsapp=NoOpSender("WHATSAPP")))
        self.db = SessionLocal()
        seed_reference(self.db)
        seed_users(self.db)
        for g in self.db.scalars(select(Gate)).all():
            g.direction = "BOTH"  # both gates carry both directions in this layout
        self.db.commit()
        self.users = {u.username: u for u in self.db.scalars(select(User)).all()}
        self.sup = self.users["sup1"]
        self.guard = self.users["guard1"]
        self.dishonest = "w2"
        self.counts: Counter = Counter()
        self.queue: list = []
        self.seq = itertools.count()
        self.outage = (datetime.combine(day, time(8, 10), tzinfo=IST), datetime.combine(day, time(8, 30), tzinfo=IST))
        self.expected_paid = 0
        self.pocketed = 0

    # --------------------------------------------------------------- scheduling
    def at(self, when: datetime, fn: Callable, *args) -> None:
        heapq.heappush(self.queue, (when, next(self.seq), fn, args))

    def run_queue(self) -> None:
        while self.queue:
            when, _, fn, args = heapq.heappop(self.queue)
            self.clock.now = when
            self.gw.online = not (self.outage[0] <= when < self.outage[1])
            fn(*args)

    # --------------------------------------------------------------- setup
    def setup_population(self) -> list[Rider]:
        rng = self.rng
        used: set[str] = set()
        riders: list[Rider] = []
        n_pass = int(self.n * 0.40)
        n_one = int(self.n * 0.02)
        for i in range(self.n):
            kind = "PASS" if i < n_pass else ("ONETIME" if i < n_pass + n_one else "WALKIN")
            r = Rider(make_plate(rng, used), kind)
            if rng.random() < 0.55:
                r.phone = f"9{rng.randint(100000000, 999999999)}"
            riders.append(r)
        rng.shuffle(riders)
        # zones & shifts: morning 06:00-14:00, evening 14:00-23:59
        zones = {z.gate_id: z for z in self.db.scalars(select(Zone)).all()}
        d0 = datetime.combine(self.day, time(0), tzinfo=IST)
        plan = [("w1", "G1", 6, 14), ("w2", "G2", 6, 14), ("w3", "G1", 14, 24), ("w4", "G2", 14, 24)]
        for uname, gate, h0, h1 in plan:
            self.db.add(ZoneAssignment(zone_id=zones[gate].id, user_id=self.users[uname].id,
                                       starts_at=d0 + timedelta(hours=h0), ends_at=d0 + timedelta(hours=h1),
                                       shift_label="MORNING" if h0 == 6 else "EVENING", assigned_by=self.sup.id))
        self.plan = plan
        self.zone_of_gate = {g: zones[g].id for g in zones}
        # pass holders bought their monthly pass last week (UPI)
        pt = self.db.scalars(select(PassType).where(PassType.vehicle_class == "BIKE", PassType.is_default)).one()
        self.gw.auto_pay = True
        self.clock.now = d0 - timedelta(days=6)
        for r in riders:
            if r.kind != "PASS":
                continue
            v = sess_svc.get_or_create_vehicle(self.db, r.plate, "BIKE", self.clock.now)
            v.phone = r.phone
            q = payments.quote_pass(self.db, v.id, pt.id)
            p = payments.create_pending_pass(self.db, v.id, pt.id, user_id=None, channel="SELF_PAY")
            pay = payments.start_upi(self.db, q, user=None, channel="SELF_PAY", purpose="PASS", pass_id=p.id)
            payments.check_upi_status(self.db, pay)
        self.gw.auto_pay = False
        # some returning walk-ins carry dues from earlier visits (spec: dues shown at entry)
        for r in riders:
            if r.kind == "WALKIN" and rng.random() < 0.08:
                v = sess_svc.get_or_create_vehicle(self.db, r.plate, "BIKE", self.clock.now)
                v.phone = r.phone
                r.prior_dues_paise = self._historic_visit(v, d0 - timedelta(days=2))
        self.db.commit()
        self.pass_type = pt
        return riders

    def _historic_visit(self, v: Vehicle, when: datetime) -> int:
        """A past visit that left ₹10–₹20 unpaid (entered, paid 2 h, stayed ~5 h)."""
        self.clock.now = when
        s = ParkingSession(vehicle_id=v.id, vehicle_class="BIKE", entry_at=when, status=SessionStatus.OPEN,
                           tariff_id=get_tariff(self.db, "BIKE", when).id, entry_gate="G1")
        self.db.add(s)
        self.db.flush()
        self.clock.now = when + timedelta(minutes=10)
        payments.record_cash(self.db, quote_session(self.db, s.id, 120), user=self.users["w1"])
        sh = cash.current_shift(self.db, self.users["w1"].id)
        self.clock.now = when + timedelta(hours=5)
        sess_svc.close_session(self.db, s, self.clock.now)
        h = cash.cash_in_hand(self.db, self.users["w1"].id, sh.id)
        ho = cash.declare_handover(self.db, self.users["w1"], h, {"10": h // 1000})
        cash.confirm_handover(self.db, ho.id, self.sup, {"10": h // 1000}, photo_path="hist.jpg")
        cash.close_shift(self.db, self.users["w1"])
        return v.balance_paise

    # --------------------------------------------------------------- per-rider plan
    def plan_day(self, riders: list[Rider]) -> None:
        rng = self.rng
        buttons = [120, 240, 480, 720, 1440]
        entries: list[Rider] = []
        for r in riders:
            r.entry_at = sample_entry(rng, self.day)
            r.exit_at = min(r.entry_at + sample_stay(rng, r.entry_at),
                            datetime.combine(self.day, time(23, 30), tzinfo=IST))
            r.entry_gate = rng.choice(["G1", "G1", "G2"])
            r.exit_gate = rng.choice(["G1", "G2", "G2"])
            # 8 % of plate reads are unreadable or wrong (split across entry and exit)
            for side in ("entry", "exit"):
                x = rng.random()
                if x < 0.02:
                    setattr(r, f"{side}_read", "UNREAD")
                elif x < 0.04:
                    setattr(r, f"{side}_read", "MISREAD")
                    setattr(r, f"{side}_misread", misread(rng, r.plate))
            if r.kind == "PASS":
                r.pay_mode = "NONE"
            else:
                x = rng.random()
                r.pay_mode = "NONE" if x < 0.012 else ("SELF" if x < 0.06 else ("CASH" if x < 0.06 + 0.25 * 0.94 else "UPI"))
                r.underpay = r.kind == "ONETIME" or rng.random() < 0.10
                if r.pay_mode == "UPI" and self.outage[0] <= r.entry_at + timedelta(minutes=5) < self.outage[1] and rng.random() < 0.05:
                    r.fake_offline_claim = True
            entries.append(r)
        # side-by-side pairs: snap ~30 % of entries to share the previous rider's instant and gate
        entries.sort(key=lambda r: r.entry_at)
        for a, b in zip(entries, entries[1:]):
            if rng.random() < 0.3 and (b.entry_at - a.entry_at) < timedelta(minutes=2):
                b.entry_at, b.entry_gate = a.entry_at, a.entry_gate
                self.counts["side_by_side_pairs"] += 1
        for r in entries:
            self.at(r.entry_at, self.do_entry, r)
            self.at(r.exit_at, self.do_exit, r)
        self.buttons = buttons
        # shift start/end, deposits
        d0 = datetime.combine(self.day, time(0), tzinfo=IST)
        for uname, gate, h0, h1 in self.plan:
            self.at(d0 + timedelta(hours=h0, seconds=1), self.open_shift, uname, gate)
            self.at(d0 + timedelta(hours=h1) - (timedelta(minutes=1) if h1 == 24 else timedelta(0)), self.close_shift, uname)
        self.at(d0 + timedelta(hours=8, minutes=45), self.run_reconciliation)
        self.at(d0 + timedelta(hours=12), self.run_reconciliation)
        self.at(d0 + timedelta(hours=23, minutes=59), self.end_of_day)

    # --------------------------------------------------------------- actions
    def _event(self, r: Rider, direction: str, gate: str, read: str, wrong: Optional[str]) -> sess_svc.ProcessResult:
        plate = None if read == "UNREAD" else (wrong if read == "MISREAD" else r.plate)
        conf = 0.0 if plate is None else (self.rng.uniform(0.62, 0.86) if read == "MISREAD" else self.rng.uniform(0.9, 0.99))
        payload = {"event_id": str(uuid.uuid4()), "gate_id": gate, "camera_ids": [f"{gate}-{self.rng.choice('LR')}"],
                   "direction": direction, "vehicle_class": "BIKE", "ts_ms": int(self.clock.now.timestamp() * 1000),
                   "status": "UNREAD" if plate is None else "READ", "plate": plate, "confidence": conf,
                   "candidates": [{"plate": plate, "confidence": conf}] if plate else [],
                   "images": {"plate_crop": f"sim/{uuid.uuid4().hex}.jpg"}, "latency_ms": self.rng.randint(400, 1300)}
        res = sess_svc.ingest_event(self.db, payload)
        self.db.commit()
        self.counts[f"{direction}_{res.event.status}"] += 1
        return res

    def do_entry(self, r: Rider) -> None:
        res = self._event(r, "IN", r.entry_gate, r.entry_read, r.entry_misread)
        if res.event.status == EventStatus.UNREAD:
            # supervisor types the plate from the image a few minutes later
            self.at(self.clock.now + timedelta(minutes=4), self.review_unread, res.event.id, r)
            return
        self._after_entry(r, res.session)

    def review_unread(self, event_id: str, r: Rider) -> None:
        res = sess_svc.resolve_event(self.db, event_id, user_id=self.sup.id, plate=r.plate, note="read from image")
        self.db.commit()
        self.counts["review_resolved"] += 1
        if res.event.direction == "IN":
            self._after_entry(r, res.session)
        else:
            self._after_exit(r, res)

    def _after_entry(self, r: Rider, s: Optional[ParkingSession]) -> None:
        if s is None:
            return
        r.session_id = s.id
        if s.status == SessionStatus.PASS:
            self.counts["pass_entries"] += 1
            return
        if r.pay_mode == "NONE":
            self.counts["missed_collections"] += 1
            return
        delay = timedelta(minutes=self.rng.uniform(2, 25))
        if r.entry_at + delay >= r.exit_at:
            delay = (r.exit_at - r.entry_at) / 2
        self.at(self.clock.now + delay, self.collect, r)

    def worker_for(self, gate: str) -> User:
        h = self.clock.now.astimezone(IST).hour
        for uname, g, h0, h1 in self.plan:
            if g == gate and h0 <= h < h1:
                return self.users[uname]
        return self.users["w1"]

    def collect(self, r: Rider) -> None:
        s = self.db.get(ParkingSession, r.session_id)
        if s.status not in (SessionStatus.OPEN,):
            return
        if s.vehicle.plate != r.plate:
            # worker compares the plate on the bike with the app and corrects the ANPR misread
            try:
                sess_svc.correct_session_plate(self.db, s.id, r.plate, user_id=self.worker_for(s.entry_gate).id)
                self.db.commit()
                self.counts["worker_corrections"] += 1
            except ValueError:
                self.db.rollback()
            s = self.db.get(ParkingSession, r.session_id)
            if s.status != SessionStatus.OPEN:
                return
        stay = r.exit_at - r.entry_at
        dur = duration_for(stay, self.buttons)
        if r.underpay:
            dur = self.buttons[max(0, self.buttons.index(dur) - (1 if dur > 120 else 0))]
            if dur == duration_for(stay, self.buttons):
                dur = 120
            self.counts["underpay_attempts"] += 1
        q = quote_session(self.db, s.id, dur)
        if q.amount_paise <= 0:
            return
        worker = self.worker_for(self.db.get(Zone, s.zone_id).gate_id if s.zone_id else "G1")
        if r.pay_mode == "SELF":
            self._self_pay(r, s, q)
            return
        if r.kind == "WALKIN" and not r.underpay and self.rng.random() < 0.02:
            self._sell_pass(r, s, worker)
            return
        if r.pay_mode == "CASH":
            if worker.username == self.dishonest and self.rng.random() < 0.05:
                r.pocketed = True  # takes the cash, records nothing, no receipt
                self.pocketed += q.amount_paise
                self.counts["pocketed"] += 1
                return
            self._ensure_cash_capacity(worker, q.amount_paise)
            try:
                p = payments.record_cash(self.db, q, user=worker, phone=r.phone if self.rng.random() < 0.8 else None)
            except CashLimitReached:
                self.counts["cash_blocked"] += 1
                return
            if p.receipt_id:
                from app.models import Receipt

                rec = self.db.get(Receipt, p.receipt_id)
                if rec.channel == "QR":
                    rec.delivery_status = "SHOWN"
            self.db.commit()
            r.paid_paise += p.amount_paise
            self.expected_paid += p.amount_paise
            self.counts["cash_payments"] += 1
            return
        # UPI
        p = payments.start_upi(self.db, q, user=worker, phone=r.phone)
        self.db.commit()
        if p.offline:
            payments.claim_offline(self.db, p, worker)
            self.db.commit()
            self.counts["upi_offline_claims"] += 1
            if r.fake_offline_claim:
                self.counts["fake_offline_claims"] += 1
                return  # customer showed a fake success screen; money never arrives
            self.gw.credit_offline(p.txn_ref, p.amount_paise, at=self.clock.now)
        else:
            body, headers = self.gw.webhook_body(self.gw.pay(p.txn_ref, at=self.clock.now))
            payments.handle_webhook(self.db, body, headers)
            self.db.commit()
            self.counts["upi_payments"] += 1
        r.paid_paise += p.amount_paise
        self.expected_paid += p.amount_paise

    def _sell_pass(self, r: Rider, s: ParkingSession, worker: User) -> None:
        """Worker pitches the monthly pass; the session in progress becomes a pass session."""
        q = payments.quote_pass(self.db, s.vehicle_id, self.pass_type.id)
        p = payments.create_pending_pass(self.db, s.vehicle_id, self.pass_type.id, user_id=worker.id, channel="WORKER")
        pay = payments.start_upi(self.db, q, user=worker, purpose="PASS", pass_id=p.id, phone=r.phone)
        if pay.offline:
            payments.claim_offline(self.db, pay, worker)
            self.gw.credit_offline(pay.txn_ref, pay.amount_paise, at=self.clock.now)
        else:
            body, headers = self.gw.webhook_body(self.gw.pay(pay.txn_ref, at=self.clock.now))
            payments.handle_webhook(self.db, body, headers)
        self.db.commit()
        r.kind = "PASS_NEW"
        r.paid_paise += q.amount_paise
        self.expected_paid += q.amount_paise
        self.counts["passes_sold"] += 1

    def _self_pay(self, r: Rider, s: ParkingSession, q: payments.Quote) -> None:
        """Customer pays on the cloud relay page; the edge receives SELF_PAY_PAID on its next pull."""
        ref = payments.make_txn_ref("S", s.id)
        msg = {"id": uuid.uuid4().hex, "kind": "SELF_PAY_PAID", "payload": {
            "plate": r.plate, "session_id": s.id, "vehicle_class": "BIKE", "duration_minutes": q.duration_minutes,
            "amount_paise": q.amount_paise, "base_paise": q.base_paise, "dues_paise": q.dues_paise, "txn_ref": ref,
            "gateway_ref": "pay_" + uuid.uuid4().hex[:12], "utr": str(self.rng.randint(10**11, 10**12 - 1)),
            "phone": r.phone, "phone_verified": bool(r.phone), "paid_at": self.clock.now.isoformat()}}
        relay_sync.apply_message(self.db, msg)
        self.gw.credit_offline(ref, q.amount_paise, at=self.clock.now)  # same merchant account settles it
        assert relay_sync.apply_message(self.db, msg)["status"] == "already_applied"
        self.db.commit()
        r.paid_paise += q.amount_paise
        self.expected_paid += q.amount_paise
        self.counts["self_pay"] += 1

    def _ensure_cash_capacity(self, worker: User, amount: int) -> None:
        h = cash.holding_dict(self.db, worker.id)
        if h["cash_in_hand_paise"] + amount > h["limit_paise"] or h["warn"]:
            self._handover(worker, h["cash_in_hand_paise"])

    def _handover(self, worker: User, amount: int) -> None:
        if amount <= 0:
            return
        denoms = {"10": amount // 1000, "5": (amount % 1000) // 500}
        ho = cash.declare_handover(self.db, worker, amount, denoms)
        cash.confirm_handover(self.db, ho.id, self.sup, denoms, photo_path=f"handover/{ho.id}.jpg")
        self.db.commit()
        self.counts["handovers"] += 1

    def do_exit(self, r: Rider) -> None:
        res = self._event(r, "OUT", r.exit_gate, r.exit_read, r.exit_misread)
        if res.event.status == EventStatus.UNREAD:
            self.at(self.clock.now + timedelta(minutes=6), self.review_unread, res.event.id, r)
            return
        if res.event.status == EventStatus.REVIEW:
            # supervisor picks the right session / plate from the images
            self.at(self.clock.now + timedelta(minutes=6), self.review_exit, res.event.id, r)
            return
        self._after_exit(r, res)

    def review_exit(self, event_id: str, r: Rider) -> None:
        res = sess_svc.resolve_event(self.db, event_id, user_id=self.sup.id, plate=r.plate, note="checked image")
        self.db.commit()
        self.counts["review_resolved"] += 1
        self._after_exit(r, res)

    def _after_exit(self, r: Rider, res: sess_svc.ProcessResult) -> None:
        if res.alert is None:
            return
        self.counts["exit_alerts"] += 1
        # the guard sees the red light; if the customer says they paid cash, one tap raises a dispute
        says_paid = r.pocketed or (r.pay_mode == "NONE" and self.rng.random() < 0.15)
        if says_paid:
            disputes.raise_dispute(self.db, vehicle_id=res.alert.vehicle_id, session_id=res.alert.session_id,
                                   raised_by_role=Role.GUARD, raised_by_user=self.guard.id,
                                   claimed_paise=None, claimed_mode="CASH", alert_id=res.alert.id,
                                   note="customer says paid cash to the zone worker")
            res.alert.acknowledged_by, res.alert.acknowledged_at = self.guard.id, self.clock.now
            self.db.commit()
            self.counts["disputes_raised"] += 1

    def open_shift(self, uname: str, gate: str) -> None:
        cash.open_shift(self.db, self.users[uname], zone_id=self.zone_of_gate[gate])
        self.db.commit()

    def close_shift(self, uname: str) -> None:
        u = self.users[uname]
        self._handover(u, cash.cash_in_hand(self.db, u.id))
        cash.close_shift(self.db, u)
        self.db.commit()

    def run_reconciliation(self) -> None:
        if not self.gw.online:
            return
        payments.reconcile_upi(self.db, self.gw.fetch_settlements(self.day))
        reports.flag_unpaid_sessions(self.db)
        self.db.commit()

    def end_of_day(self) -> None:
        self.run_reconciliation()
        # supervisor reviews disputes: repeated "paid cash" claims in one zone are left UNRESOLVED
        # (CCTV check pending); isolated ones without evidence are REJECTED
        by_worker: Counter = Counter()
        open_d = self.db.scalars(select(disputes.PaymentDispute).where(disputes.PaymentDispute.status == "OPEN")).all()
        for d in open_d:
            by_worker[d.worker_id] += 1
        for d in open_d:
            outcome = "UNRESOLVED" if by_worker[d.worker_id] >= 3 else "REJECTED"
            disputes.resolve_dispute(self.db, d.id, self.sup, outcome, "reviewed at end of day")
        # stale offline claims (fake success screens) are failed after the 24 h review
        self.clock.now += timedelta(minutes=1)
        # bank deposit of the day's handovers
        rec = cash.cash_reconciliation(self.db, self.day.isoformat())
        if rec["handed_over_paise"]:
            dep = cash.record_deposit(self.db, self.sup, self.day.isoformat(), rec["handed_over_paise"], "SLIP-SIM-1", "slip.jpg")
            cash.mark_bank_credit(self.db, dep.id, rec["handed_over_paise"])
        self.db.commit()

    # --------------------------------------------------------------- run
    def run(self) -> SimResult:
        t0 = _time.monotonic()
        riders = self.setup_population()
        self.plan_day(riders)
        self.log(f"simulating {len(riders)} vehicles on {self.day} ...")
        self.run_queue()
        # next morning: supervisor handles claims unconfirmed for 24 h
        self.clock.now = datetime.combine(self.day + timedelta(days=1), time(10), tzinfo=IST)
        self.gw.online = True
        payments.reconcile_upi(self.db, self.gw.fetch_settlements(self.day))
        stale = payments.stale_offline_claims(self.db)
        for p in stale:
            payments.fail_payment(self.db, p, "not in settlement after 24 h")
        self.counts["stale_claims_failed"] = len(stale)
        self.db.commit()
        day = self.day.isoformat()
        upi = payments.reconcile_upi(self.db, self.gw.fetch_settlements(self.day), day=day)
        self.db.commit()
        # a week later the one-time visitors' dues are still open -> unrecovered
        self.clock.now = datetime.combine(self.day + timedelta(days=8), time(10), tzinfo=IST)
        res = SimResult(
            day=day, riders=riders, counts=self.counts, expected_payments_paise=self.expected_paid,
            pocketed_paise=self.pocketed, dishonest_worker=self.dishonest,
            worker_report=reports.worker_comparison(self.db, day),
            cash_recon=cash.cash_reconciliation(self.db, day), upi_recon=upi,
            review_counts=reports.review_queue_counts(self.db), revenue=reports.daily_revenue(self.db, day),
            defaulters=reports.defaulters(self.db, 0), pass_report=reports.pass_report(self.db, day),
            ledger_mismatches=ledger.verify_balances(self.db),
            disputes=reports.disputes_by_worker(self.db, day), anpr_accuracy=reports.anpr_accuracy(self.db, day),
            elapsed_s=_time.monotonic() - t0)
        return res


def run_simulation(**kw) -> tuple[SimResult, Simulator]:
    sim = Simulator(**kw)
    return sim.run(), sim


def summary(res: SimResult) -> str:
    rev = res.revenue[0] if res.revenue else {}
    lines = [
        f"Day {res.day}: {len(res.riders)} vehicles simulated in {res.elapsed_s:.1f} s",
        f"  events: {dict(sorted((k, v) for k, v in res.counts.items() if k.startswith(('IN_', 'OUT_'))))}",
        f"  side-by-side pairs: {res.counts['side_by_side_pairs']}, pass entries: {res.counts['pass_entries']}",
        f"  payments: UPI {res.counts['upi_payments']}, offline UPI claims {res.counts['upi_offline_claims']} "
        f"(fake {res.counts['fake_offline_claims']}), cash {res.counts['cash_payments']}, self-pay {res.counts['self_pay']}",
        f"  revenue: walk-in UPI ₹{rev.get('walkin_upi_paise', 0) / 100:,.0f}, walk-in cash ₹{rev.get('walkin_cash_paise', 0) / 100:,.0f}, "
        f"pass ₹{(rev.get('pass_upi_paise', 0) + rev.get('pass_cash_paise', 0)) / 100:,.0f}, charges ₹{rev.get('charges_paise', 0) / 100:,.0f}",
        f"  cash: collected ₹{res.cash_recon['collected_paise'] / 100:,.0f}, handed over ₹{res.cash_recon['handed_over_paise'] / 100:,.0f}, "
        f"deposited ₹{res.cash_recon['deposited_paise'] / 100:,.0f}, credited ₹{res.cash_recon['bank_credited_paise'] / 100:,.0f}",
        f"  dishonest worker {res.dishonest_worker} pocketed {res.counts['pocketed']} payments (₹{res.pocketed_paise / 100:,.0f})",
        f"  exit alerts {res.counts['exit_alerts']}, disputes {res.counts['disputes_raised']}, handovers {res.counts['handovers']}",
        f"  review queue now: {res.review_counts}; reviews resolved during day: {res.counts['review_resolved']}",
        f"  UPI reconciliation: mismatches {len(res.upi_recon['amount_mismatch'])}, unknown credits {len(res.upi_recon['unknown_credits'])}, "
        f"missing {len(res.upi_recon['missing_from_settlement'])}",
        f"  unrecovered one-time dues: {len(res.defaulters['unrecovered_one_time'])} vehicles ₹{res.defaulters['unrecovered_total_paise'] / 100:,.0f}",
        "  worker comparison:",
    ]
    for row in res.worker_report["rows"]:
        lines.append(f"    {row['name']:<16} zone {row['zone_id']} collections {row['collections']:>4} cash {row['cash_share']:.0%} "
                     f"unpaid-in-zone {row['unpaid_rate']:.1%} disputes {row['disputes_total']} flags {row['flags']}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vehicles", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--db", default=None, help="SQLAlchemy URL (default: in-memory SQLite)")
    args = ap.parse_args()
    res, _ = run_simulation(n_vehicles=args.vehicles, seed=args.seed, db_url=args.db)
    print(summary(res))


if __name__ == "__main__":
    main()
