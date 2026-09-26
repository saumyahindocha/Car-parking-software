from datetime import timedelta

import pytest
from sqlalchemy import select

from app.adapters.gateway import SettlementLine
from app.domain import cash, ledger, payments
from app.domain.payments import CashLimitReached, PaymentError, quote_session
from app.domain.settings import set_setting
from app.models import (Override, ParkingSession, Payment, PayStatus, Receipt, SessionStatus, User, Vehicle)
from app.security import hash_secret

from .conftest import ist
from .helpers import event

T0 = ist(2026, 3, 10, 8, 0)


def user(db, name):
    return db.scalars(select(User).where(User.username == name)).one()


def test_upi_online_confirmed_only_by_webhook(db, gateway, messenger):
    r = event(db, "IN", "MH43AB1234", T0)
    p = payments.start_upi(db, quote_session(db, r.session.id, 240), user=user(db, "w1"))
    assert p.status == PayStatus.INITIATED and p.gateway_order_id and not p.offline
    assert db.get(Vehicle, r.session.vehicle_id).balance_paise == 0          # nothing posted yet
    wev = gateway.pay(p.txn_ref)
    body, headers = gateway.webhook_body(wev)
    payments.handle_webhook(db, body, headers)
    assert p.status == PayStatus.CONFIRMED and p.receipt_id
    assert db.get(Vehicle, r.session.vehicle_id).balance_paise == -2000
    assert r.session.status == SessionStatus.PREPAID
    # replayed webhook is idempotent
    payments.handle_webhook(db, body, headers)
    assert ledger.balance(db, r.session.vehicle_id) == -2000
    with pytest.raises(PermissionError):
        payments.handle_webhook(db, body, {"x-mock-signature": "forged"})


def test_upi_status_poll(db, gateway, messenger):
    r = event(db, "IN", "MH43AB1234", T0)
    p = payments.start_upi(db, quote_session(db, r.session.id, 120), user=user(db, "w1"))
    payments.check_upi_status(db, p)
    assert p.status == PayStatus.INITIATED
    gateway.pay(p.txn_ref)
    payments.check_upi_status(db, p)
    assert p.status == PayStatus.CONFIRMED


def test_offline_upi_claim_then_reconciliation(db, gateway, messenger):
    gateway.online = False
    r = event(db, "IN", "MH43AB1234", T0)
    p = payments.start_upi(db, quote_session(db, r.session.id, 120), user=user(db, "w1"))
    assert p.offline and p.upi_uri.startswith("upi://pay?") and f"tr={p.txn_ref}" in p.upi_uri
    payments.claim_offline(db, p, user(db, "w1"))
    assert p.status == PayStatus.CLAIMED_OFFLINE
    out = event(db, "OUT", "MH43AB1234", T0 + timedelta(hours=1))
    assert out.exit_display["state"] == "GREEN"                               # claim counts provisionally
    # a claim synced from a phone that was fully offline
    r2 = event(db, "IN", "MH01XY0001", T0)
    p2 = payments.record_offline_claim(db, user=user(db, "w1"), client_uuid="dev-1", amount_paise=1000,
                                       txn_ref="PS" + "ZZZX" + "ABC123", client_created_at=T0 + timedelta(minutes=5),
                                       session_id=r2.session.id, duration_minutes=120)
    assert payments.record_offline_claim(db, user=user(db, "w1"), client_uuid="dev-1", amount_paise=1000,
                                         txn_ref="x", client_created_at=T0, session_id=r2.session.id,
                                         duration_minutes=120).id == p2.id                 # idempotent
    gateway.online = True
    gateway.credit_offline(p.txn_ref, 1000)
    rep = payments.reconcile_upi(db, gateway.fetch_settlements(T0.date()))
    assert p.id in rep["confirmed_offline"] and p.status == PayStatus.CONFIRMED
    assert p2.status == PayStatus.CLAIMED_OFFLINE                              # never paid
    stale = payments.stale_offline_claims(db, now=T0 + timedelta(hours=30))
    assert p2 in stale
    payments.fail_payment(db, p2, "not in bank statement")
    assert db.get(ParkingSession, r2.session.id).status == SessionStatus.OPEN


def test_offline_claim_amount_must_match_tariff(db, gateway, messenger):
    r = event(db, "IN", "MH43AB1234", T0)
    with pytest.raises(PaymentError):
        payments.record_offline_claim(db, user=user(db, "w1"), client_uuid="dev-2", amount_paise=5000, txn_ref="PSX",
                                      client_created_at=T0, session_id=r.session.id, duration_minutes=120)


def test_cash_limit_blocks_and_warns(db, gateway, messenger):
    set_setting(db, "cash_limit_paise", 3000)
    w = user(db, "w1")
    sessions = [event(db, "IN", f"MH43AB{1000 + i}", T0 + timedelta(minutes=i)).session for i in range(4)]
    payments.record_cash(db, quote_session(db, sessions[0].id, 120), user=w)
    payments.record_cash(db, quote_session(db, sessions[1].id, 120), user=w)
    h = cash.holding_dict(db, w.id)
    assert h["cash_in_hand_paise"] == 2000 and not h["warn"]
    payments.record_cash(db, quote_session(db, sessions[2].id, 120), user=w)
    h = cash.holding_dict(db, w.id)
    assert h["warn"] and h["blocked"]
    with pytest.raises(CashLimitReached):
        payments.record_cash(db, quote_session(db, sessions[3].id, 120), user=w)
    payments.start_upi(db, quote_session(db, sessions[3].id, 120), user=w)    # UPI still works
    # offline sync bypasses the server check but is flagged
    p = payments.record_cash(db, quote_session(db, sessions[3].id, 120), user=w, client_uuid="c-1",
                             client_created_at=T0 + timedelta(minutes=30), offline_sync=True)
    assert p.limit_breach
    # handover resets cash in hand
    sup = user(db, "sup1")
    ho = cash.declare_handover(db, w, 4000, {"500": 0, "20": 1, "10": 2})
    with pytest.raises(cash.CashError):
        cash.confirm_handover(db, ho.id, sup, {"20": 1, "10": 2})                     # no photo
    cash.confirm_handover(db, ho.id, sup, {"20": 1, "10": 2}, photo_path="p.jpg")
    assert cash.cash_in_hand(db, w.id) == 0


def test_cash_amount_is_system_calculated_and_override_needs_pin(db, gateway, messenger):
    r = event(db, "IN", "MH43AB1234", T0)
    q = quote_session(db, r.session.id, 120)
    with pytest.raises(PaymentError):
        payments.record_cash(db, q, user=user(db, "w1"), override_paise=500, override_reason="regular")
    q = quote_session(db, r.session.id, 120)
    with pytest.raises(PaymentError):
        payments.record_cash(db, q, user=user(db, "w1"), override_paise=500, override_reason="x", supervisor_pin="9999")
    q = quote_session(db, r.session.id, 120)
    p = payments.record_cash(db, q, user=user(db, "w1"), override_paise=500, override_reason="elderly", supervisor_pin="1234")
    assert p.amount_paise == 500 and db.get(Override, p.override_id).approved_by == user(db, "sup1").id


def test_cash_receipt_digital_and_phone(db, gateway, messenger):
    r = event(db, "IN", "MH43AB1234", T0)
    p = payments.record_cash(db, quote_session(db, r.session.id, 120), user=user(db, "w1"), phone="9876543210")
    rec = db.get(Receipt, p.receipt_id)
    assert rec.channel == "SMS" and rec.data["amount_paise"] == 1000 and rec.data["link"].endswith(rec.code)
    assert "Final charge is calculated on actual time" in rec.data["footer"]
    from app.domain import notify

    notify.deliver_pending(db)
    assert rec.delivery_status == "SENT" and messenger.sms.sent
    r2 = event(db, "IN", "MH43AB9999", T0)
    p2 = payments.record_cash(db, quote_session(db, r2.session.id, 120), user=user(db, "w1"))
    assert db.get(Receipt, p2.receipt_id).channel == "QR"


def test_cash_switch_and_desk_only(db, gateway, messenger):
    r = event(db, "IN", "MH43AB1234", T0)
    set_setting(db, "cash_enabled", False)
    with pytest.raises(PaymentError):
        payments.record_cash(db, quote_session(db, r.session.id, 120), user=user(db, "w1"))
    set_setting(db, "cash_enabled", True)
    set_setting(db, "cash_desk_only", True)
    set_setting(db, "cash_desk_user_ids", [user(db, "w2").id])
    with pytest.raises(PaymentError):
        payments.record_cash(db, quote_session(db, r.session.id, 120), user=user(db, "w1"))
    payments.record_cash(db, quote_session(db, r.session.id, 120), user=user(db, "w2"))


def test_only_supervisor_reverses_cash(db, gateway, messenger):
    r = event(db, "IN", "MH43AB1234", T0)
    p = payments.record_cash(db, quote_session(db, r.session.id, 120), user=user(db, "w1"))
    with pytest.raises(PermissionError):
        payments.reverse_cash(db, p.id, supervisor=user(db, "w1"), reason="oops")
    with pytest.raises(PaymentError):
        payments.reverse_cash(db, p.id, supervisor=user(db, "sup1"), reason="")
    payments.reverse_cash(db, p.id, supervisor=user(db, "sup1"), reason="recorded on wrong plate")
    assert p.status == PayStatus.REVERSED and ledger.balance(db, p.vehicle_id) == 0


def test_upi_refund(db, gateway, messenger):
    gateway.auto_pay = True
    r = event(db, "IN", "MH43AB1234", T0)
    p = payments.start_upi(db, quote_session(db, r.session.id, 120), user=user(db, "w1"))
    payments.check_upi_status(db, p)
    payments.refund_upi(db, p.id, supervisor=user(db, "sup1"), reason="duplicate payment")
    assert p.status == PayStatus.REFUNDED and gateway.refunds and ledger.balance(db, p.vehicle_id) == 0


def test_shift_close_and_cash_reconciliation(db, gateway, messenger):
    w, sup = user(db, "w1"), user(db, "sup1")
    cash.open_shift(db, w, at=T0)
    for i in range(3):
        s = event(db, "IN", f"MH43AB{2000 + i}", T0 + timedelta(minutes=i)).session
        payments.record_cash(db, quote_session(db, s.id, 120), user=w, client_created_at=T0 + timedelta(minutes=i + 1),
                             client_uuid=f"u{i}")
    with pytest.raises(cash.CashError):
        cash.close_shift(db, w)
    ho = cash.declare_handover(db, w, 3000, {"10": 3})
    cash.confirm_handover(db, ho.id, sup, {"10": 2}, photo_path="p.jpg", note="one note short")
    sh = cash.close_shift(db, w, note="₹10 short, explained")
    assert sh.variance_paise == -1000
    ho.confirmed_at = T0 + timedelta(hours=8)
    date = T0.date().isoformat()
    dep = cash.record_deposit(db, sup, date, 2000, "SLIP-1", "slip.jpg")
    cash.mark_bank_credit(db, dep.id, 2000)
    rec = cash.cash_reconciliation(db, date)
    assert rec["collected_paise"] == 3000 and rec["handed_over_paise"] == 2000
    assert rec["gap_collected_vs_handed_paise"] == 1000 and rec["gap_handed_vs_deposited_paise"] == 0
    assert rec["gap_deposited_vs_credited_paise"] == 0
