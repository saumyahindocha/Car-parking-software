import uuid
from datetime import timedelta

from app import relay_sync
from app.models import Alert, Payment, PayStatus

from app.db import utcnow

from .conftest import ist
from .helpers import event


def test_self_pay_message_applies_once_and_push_contains_quotes(db, gateway, messenger):
    r = event(db, "IN", "MH43AB1234", utcnow() - timedelta(minutes=30))
    push = relay_sync.build_push(db, None)
    e = [x for x in push["entries"] if x["session_id"] == r.session.id][0]
    assert e["masked_plate"] == "MH43••••34" and e["quotes"]["120"] == 1000
    msg = {"id": uuid.uuid4().hex, "kind": "SELF_PAY_PAID", "payload": {
        "plate": "MH43AB1234", "session_id": r.session.id, "duration_minutes": 120, "amount_paise": 1000,
        "txn_ref": "PS1X0A0B0C", "gateway_ref": "pay_1", "utr": "123", "phone": "9876543210"}}
    assert relay_sync.apply_message(db, msg)["status"] == "applied"
    assert relay_sync.apply_message(db, msg)["status"] == "already_applied"
    p = db.query(Payment).filter_by(txn_ref="PS1X0A0B0C").one()
    assert p.status == PayStatus.CONFIRMED and p.channel == "SELF_PAY"
    push2 = relay_sync.build_push(db, None)
    assert any(x["data"]["amount_paise"] == 1000 for x in push2["receipts"])
    v = [x for x in push2["vehicles"] if x["plate"] == "MH43AB1234"][0]
    assert v["phone_hash"] == relay_sync.phone_hash("9876543210") and v["balance_paise"] == -1000


def test_dispute_and_data_request_messages(db, gateway, messenger):
    event(db, "IN", "MH43AB1234", ist(2026, 3, 10, 8))
    out = relay_sync.apply_message(db, {"id": "m1", "kind": "DISPUTE", "payload": {"plate": "MH43AB1234",
                                                                                   "claimed_mode": "CASH"}})
    assert out["dispute_id"]
    out = relay_sync.apply_message(db, {"id": "m2", "kind": "DATA_REQUEST", "payload": {"plate": "MH43AB1234",
                                                                                        "request_type": "erase"}})
    assert db.get(Alert, out["alert_id"]).kind == "DATA_REQUEST"
