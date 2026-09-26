"""Full-day integration test (spec section 15): 3,000 bikes through the whole backend."""
import os
from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from app.db import set_clock
from app.domain.tariff import TariffSpec, calculate_charge
from app.models import (AnprEvent, LedgerEntry, LedgerKind, ParkingSession, Payment, PaymentDispute, PayMode, PayStatus,
                        User, Vehicle)
from sim.simulator import Simulator, summary

N = int(os.environ.get("SIM_VEHICLES", "3000"))


@pytest.fixture(scope="module")
def sim():
    s = Simulator(n_vehicles=N, seed=7, log=lambda *_: None)
    res = s.run()
    print("\n" + summary(res))
    yield res, s
    set_clock(None)


def test_ledger_integrity(sim):
    res, s = sim
    assert res.ledger_mismatches == []
    total_bal = s.db.scalar(select(func.sum(Vehicle.balance_paise)))
    assert total_bal == s.db.scalar(select(func.sum(LedgerEntry.amount_paise)))


def test_revenue_matches_what_customers_paid(sim):
    res, s = sim
    rev = res.revenue[0]
    confirmed = rev["walkin_upi_paise"] + rev["walkin_cash_paise"] + rev["pass_upi_paise"] + rev["pass_cash_paise"]
    assert confirmed == res.expected_payments_paise
    assert rev["claimed_offline_paise"] == 0  # every offline claim was reconciled or failed
    assert res.counts["upi_offline_claims"] > 20  # the outage really pushed UPI to the offline path


def test_every_rider_balance_is_exact(sim):
    res, s = sim
    tz = ZoneInfo("Asia/Kolkata")
    tariff = TariffSpec()
    wrong = []
    for r in res.riders:
        v = s.db.scalars(select(Vehicle).where(Vehicle.plate == r.plate)).first()
        assert v is not None, r.plate
        if r.kind == "PASS":
            expected = 0
        elif r.kind == "PASS_NEW":
            expected = 0  # pass sale cleared any prior dues and covered the stay
        else:
            entry = r.entry_at.replace(microsecond=(r.entry_at.microsecond // 1000) * 1000)
            exit_ = r.exit_at.replace(microsecond=(r.exit_at.microsecond // 1000) * 1000)
            expected = r.prior_dues_paise + calculate_charge("BIKE", entry, exit_, tariff, tz) - r.paid_paise
        if v.balance_paise != expected:
            wrong.append((r.plate, r.kind, r.entry_read, r.exit_read, v.balance_paise, expected))
    assert wrong == []


def test_pass_holders_zero_interaction(sim):
    res, s = sim
    assert res.counts["pass_entries"] >= int(N * 0.40)
    # net charge on every pass-covered session is zero (a same-day pass reverses an earlier charge)
    net = s.db.execute(select(LedgerEntry.session_id, func.sum(LedgerEntry.amount_paise)).join(
        ParkingSession, ParkingSession.id == LedgerEntry.session_id).where(
        LedgerEntry.kind.in_([LedgerKind.CHARGE, LedgerKind.ADJUSTMENT]), ParkingSession.pass_id.is_not(None),
        LedgerEntry.reason.is_(None) | LedgerEntry.reason.like("stay covered by pass%"))
        .group_by(LedgerEntry.session_id)).all()
    assert all(amt == 0 for _, amt in net)
    assert 0.38 <= res.pass_report["traffic_share_on_pass"] <= 0.45
    assert res.counts["passes_sold"] > 0


def test_cash_reconciliation_ties_out(sim):
    res, s = sim
    c = res.cash_recon
    assert c["collected_paise"] > 0
    assert c["collected_paise"] == c["handed_over_paise"] == c["deposited_paise"] == c["bank_credited_paise"]
    assert c["gap_collected_vs_handed_paise"] == c["gap_handed_vs_deposited_paise"] == c["gap_deposited_vs_credited_paise"] == 0
    cash_sum = s.db.scalar(select(func.sum(Payment.amount_paise)).where(Payment.mode == PayMode.CASH,
                                                                      Payment.status == PayStatus.CONFIRMED,
                                                                      Payment.created_at >= s.clock.now - timedelta(days=9)))
    assert cash_sum >= c["collected_paise"]


def test_upi_reconciliation_and_offline_claims(sim):
    res, s = sim
    u = res.upi_recon
    assert u["amount_mismatch"] == [] and u["unknown_credits"] == [] and u["missing_from_settlement"] == []
    failed = s.db.scalar(select(func.count(Payment.id)).where(Payment.status == PayStatus.FAILED))
    assert failed == res.counts["fake_offline_claims"] == res.counts["stale_claims_failed"]
    assert s.db.scalar(select(func.count(Payment.id)).where(Payment.status == PayStatus.CLAIMED_OFFLINE)) == 0


def test_review_queues(sim):
    res, s = sim
    assert res.counts["IN_UNREAD"] > 0 and res.counts["OUT_UNREAD"] > 0
    assert res.review_counts == {"unread": 0, "review": 0, "orphans": 0, "offline_claims": 0, "disputes": 0}
    # misreads were absorbed by approximate matching or worker correction, and logged
    approx = s.db.scalar(select(func.count(AnprEvent.id)).where(AnprEvent.match_type == "APPROX"))
    assert approx > 0
    acc = {a["camera_id"]: a for a in res.anpr_accuracy}
    assert acc and all(0.9 < a["read_rate"] < 1.0 for a in acc.values())
    assert res.counts["side_by_side_pairs"] > 100


def test_dishonest_worker_is_flagged(sim):
    res, s = sim
    assert res.counts["pocketed"] > 0
    rows = res.worker_report["rows"]
    bad = [r for r in rows if r["name"] == s.users[res.dishonest_worker].name]
    others = [r for r in rows if r["name"] != s.users[res.dishonest_worker].name]
    assert any("SUSPECT_UNRECORDED_CASH" in r["flags"] and "REPEATED_DISPUTES" in r["flags"] for r in bad)
    assert not any({"SUSPECT_UNRECORDED_CASH", "REPEATED_DISPUTES"} & set(r["flags"]) for r in others)
    # every pocketed customer was red-flagged at exit and their dispute is linked to the dishonest worker
    w = s.users[res.dishonest_worker]
    for r in res.riders:
        if r.pocketed:
            d = s.db.scalars(select(PaymentDispute).where(PaymentDispute.session_id == r.session_id)).first()
            assert d is not None and d.worker_id == w.id and d.raised_by_role == "GUARD"


def test_unrecovered_one_time_dues(sim):
    res, s = sim
    unrec = {x["plate"] for x in res.defaulters["unrecovered_one_time"]}
    onetime_owing = [r.plate for r in res.riders if r.kind == "ONETIME"
                     and s.db.scalars(select(Vehicle).where(Vehicle.plate == r.plate)).one().balance_paise > 0]
    assert onetime_owing and set(onetime_owing) <= unrec
    assert res.defaulters["unrecovered_total_paise"] > 0
