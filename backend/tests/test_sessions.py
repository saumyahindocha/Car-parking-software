from datetime import timedelta

from sqlalchemy import select

from app.domain import ledger, payments
from app.domain.payments import quote_session
from app.domain.sessions import resolve_event, resolve_orphan, search_plate
from app.models import (Alert, EventStatus, LedgerEntry, ParkingSession, PassType, PlateCorrection,
                        SessionStatus, User, Vehicle)

from .conftest import ist
from .helpers import event

T0 = ist(2026, 3, 10, 8, 0)


def worker(db, name="w1"):
    return db.scalars(select(User).where(User.username == name)).one()


def test_entry_exit_exact_charges_and_carries_forward(db, gateway, messenger):
    r = event(db, "IN", "MH43AB1234", T0)
    assert r.session.status == SessionStatus.OPEN and r.event.status == EventStatus.MATCHED
    q = quote_session(db, r.session.id, 120)
    assert q.amount_paise == 1000
    payments.record_cash(db, q, user=worker(db))
    assert r.session.status == SessionStatus.PREPAID
    out = event(db, "OUT", "MH43AB1234", T0 + timedelta(hours=4))       # actual 4 h -> ₹20
    assert out.session.charge_paise == 2000
    v = db.scalars(select(Vehicle).where(Vehicle.plate == "MH43AB1234")).one()
    assert v.balance_paise == 1000 == ledger.balance(db, v.id)            # ₹10 carried forward
    assert out.exit_display["state"] == "GREEN"   # session was paid; ₹10 carried forward is under the ₹20 alert threshold
    # next visit: previous dues are added to the quote
    r2 = event(db, "IN", "MH43AB1234", T0 + timedelta(days=1))
    q2 = quote_session(db, r2.session.id, 120)
    assert (q2.base_paise, q2.dues_paise, q2.amount_paise) == (1000, 1000, 2000)


def test_overpayment_becomes_credit(db, gateway, messenger):
    r = event(db, "IN", "MH12CD5678", T0)
    payments.record_cash(db, quote_session(db, r.session.id, 480), user=worker(db))  # ₹30 for 8 h
    out = event(db, "OUT", "MH12CD5678", T0 + timedelta(hours=1))
    assert out.session.status == SessionStatus.SETTLED
    assert out.exit_display["state"] == "GREEN"
    assert db.get(Vehicle, out.session.vehicle_id).balance_paise == -2000


def test_approximate_exit_match_logged(db, gateway, messenger):
    event(db, "IN", "MH43AB1234", T0)
    out = event(db, "OUT", "MH43AB1Z35", T0 + timedelta(hours=1))  # Z->2 confusion + one wrong digit
    assert out.event.status == EventStatus.MATCHED and out.event.match_type == "APPROX"
    assert out.session.vehicle.plate == "MH43AB1234"
    corr = db.scalars(select(PlateCorrection)).all()
    assert corr and corr[-1].raw_plate == "MH43AB1235" and corr[-1].chosen_plate == "MH43AB1234"


def test_ambiguous_exit_goes_to_review(db, gateway, messenger):
    event(db, "IN", "MH43AB1234", T0)
    event(db, "IN", "MH43AB1236", T0 + timedelta(minutes=1))
    out = event(db, "OUT", "MH43AB1235", T0 + timedelta(hours=1))
    assert out.event.status == EventStatus.REVIEW and out.event.review_reason == "AMBIGUOUS"
    sid = [c for c in out.event.candidates if c.get("session_id")][0]["session_id"]
    sup = worker(db, "sup1")
    res = resolve_event(db, out.event.id, user_id=sup.id, session_id=sid)
    assert res.session.status in (SessionStatus.CLOSED, SessionStatus.SETTLED)
    assert out.event.status == EventStatus.RESOLVED


def test_unread_goes_to_review_and_manual_resolution(db, gateway, messenger):
    r = event(db, "IN", None, T0, status="UNREAD", conf=0.0)
    assert r.event.status == EventStatus.UNREAD
    sup = worker(db, "sup1")
    res = resolve_event(db, r.event.id, user_id=sup.id, plate="KA01AB0001")
    assert res.session is not None and res.session.status == SessionStatus.OPEN


def test_orphan_entry_and_exit(db, gateway, messenger):
    r1 = event(db, "IN", "MH43AB1234", T0)
    r2 = event(db, "IN", "MH43AB1234", T0 + timedelta(hours=3))           # missed exit
    assert db.get(ParkingSession, r1.session.id).status == SessionStatus.ORPHAN_ENTRY
    assert r2.session.status == SessionStatus.OPEN
    sup = worker(db, "sup1")
    s = resolve_orphan(db, r1.session.id, user_id=sup.id, action="CHARGE", at=T0 + timedelta(hours=2), note="CCTV")
    assert s.charge_paise == 1000
    event(db, "OUT", "MH43AB1234", T0 + timedelta(hours=4))
    out = event(db, "OUT", "MH43AB1234", T0 + timedelta(hours=6))           # no open session, known vehicle
    assert out.session.status == SessionStatus.ORPHAN_EXIT


def test_unknown_exit_to_review(db, gateway, messenger):
    out = event(db, "OUT", "TN01ZZ9999", T0)
    assert out.event.status == EventStatus.REVIEW and out.event.review_reason == "NO_MATCH"


def test_dedupe_and_wrong_way(db, gateway, messenger):
    event(db, "IN", "MH43AB1234", T0)
    d = event(db, "IN", "MH43AB1234", T0 + timedelta(seconds=20))
    assert d.event.status == EventStatus.DUPLICATE
    w = event(db, "OUT", "MH43AB1234", T0 + timedelta(minutes=5), gate="G1")  # G1 is IN-only
    assert w.event.status == EventStatus.WRONG_WAY
    assert db.scalars(select(Alert).where(Alert.kind == "WRONG_WAY")).first() is not None


def test_pass_holder_zero_interaction_and_approx(db, gateway, messenger):
    r = event(db, "IN", "MH43PQ4321", T0)
    pt = db.scalars(select(PassType).where(PassType.vehicle_class == "BIKE", PassType.is_default)).one()
    q = payments.quote_pass(db, r.session.vehicle_id, pt.id)
    p = payments.create_pending_pass(db, r.session.vehicle_id, pt.id, user_id=None, channel="WORKER", start=T0)
    payments.record_cash(db, q, user=worker(db), purpose="PASS", pass_id=p.id)
    assert p.status == "ACTIVE"
    out = event(db, "OUT", "MH43PQ4321", T0 + timedelta(hours=9))
    assert out.session.charge_paise == 0 and out.exit_display["state"] == "GREEN"
    assert out.exit_display["pass_valid_till"] is not None
    # next day, the entry read is off by a character -> still recognised as the pass holder
    r2 = event(db, "IN", "MH43PQ4329", T0 + timedelta(days=1), conf=0.7)
    assert r2.session.status == SessionStatus.PASS and r2.event.match_type == "APPROX"
    v = db.get(Vehicle, r2.session.vehicle_id)
    assert v.balance_paise == 0
    # a confident, valid read of a neighbouring plate is a different vehicle
    r3 = event(db, "IN", "MH43PQ4322", T0 + timedelta(days=1, minutes=5), conf=0.97)
    assert r3.session.status == SessionStatus.OPEN and r3.event.match_type == "EXACT"


def test_pass_expiry_mid_stay_charges_after_expiry(db, gateway, messenger):
    r = event(db, "IN", "MH43PQ4321", T0)
    pt = db.scalars(select(PassType).where(PassType.vehicle_class == "BIKE", PassType.is_default)).one()
    p = payments.create_pending_pass(db, r.session.vehicle_id, pt.id, user_id=None, channel="WORKER", start=T0)
    payments.record_cash(db, payments.quote_pass(db, r.session.vehicle_id, pt.id), user=worker(db), purpose="PASS", pass_id=p.id)
    event(db, "OUT", "MH43PQ4321", T0 + timedelta(hours=1))
    last_evening = p.ends_at - timedelta(hours=1)
    event(db, "IN", "MH43PQ4321", last_evening)
    out = event(db, "OUT", "MH43PQ4321", p.ends_at + timedelta(hours=3))
    assert out.session.charge_paise == 1500


def test_search_plate_ranked(db, gateway, messenger):
    for pl in ("MH43AB1234", "MH43AB1235", "KA01CD0001"):
        event(db, "IN", pl, T0)
    res = search_plate(db, "MH43AB1Z34")
    assert res[0]["vehicle"].plate == "MH43AB1234" and res[0]["distance"] == 0
    assert any(r["vehicle"].plate == "MH43AB1235" for r in res)
    assert search_plate(db, "1234")[0]["vehicle"].plate == "MH43AB1234"


def test_ledger_is_append_only(db, gateway, messenger):
    import pytest

    from app.audit import AppendOnlyViolation

    event(db, "IN", "MH43AB1234", T0)
    event(db, "OUT", "MH43AB1234", T0 + timedelta(hours=1))
    db.commit()
    e = db.scalars(select(LedgerEntry)).first()
    e.amount_paise = 1
    with pytest.raises(AppendOnlyViolation):
        db.flush()
    db.rollback()
    e = db.scalars(select(LedgerEntry)).first()
    db.delete(e)
    with pytest.raises(AppendOnlyViolation):
        db.flush()
