"""End-to-end: the REAL edge backend (backend/app) syncing with this relay through relay_sync.sync_once.

The edge's RelayClient talks to the relay app in-process (its httpx client is replaced by a
TestClient, which is an httpx.Client over ASGI), so the exact wire contract is exercised.
"""
import os
import re
import sys
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path

import pytest

from .conftest import KEY, csrf, login, make_settings

BACKEND = Path(__file__).resolve().parents[2] / "backend"
IMAGE_ROOT = tempfile.mkdtemp(prefix="relay-it-images-")

# configure the edge before its settings are first read (only this test module imports the backend)
os.environ["PARK_RUN_BACKGROUND_JOBS"] = "false"
os.environ["PARK_PBKDF2_ITERATIONS"] = "1000"
os.environ["PARK_IMAGE_ROOT"] = IMAGE_ROOT
os.environ["PARK_UPLOAD_ROOT"] = tempfile.mkdtemp(prefix="relay-it-uploads-")
os.environ["PARK_RELAY_API_KEY"] = KEY
os.environ["PARK_PUBLIC_RECEIPT_BASE"] = "https://testserver/r"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

edge = pytest.importorskip("app.relay_sync", reason="edge backend (backend/app) not importable")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app import domain  # noqa: E402,F401  (registers audit + event hooks)
from app.adapters.gateway import MockGateway as EdgeMockGateway, set_gateway  # noqa: E402
from app.adapters.messaging import Messenger, NoOpSender, set_messenger  # noqa: E402
from app.config import get_settings as edge_settings  # noqa: E402
from app.db import Base as EdgeBase, SessionLocal as EdgeSession, set_engine, utcnow  # noqa: E402
from app.domain.sessions import ingest_event  # noqa: E402
from app.models import (Alert, ParkingSession, Pass, Payment, PaymentDispute, RelayInbox,  # noqa: E402
                        Vehicle as EdgeVehicle)
from app.models import PassType as EdgePassType  # noqa: E402
from app.seed import seed_reference, seed_users  # noqa: E402

from relay.main import create_app  # noqa: E402
from relay.models import Entry, Outbox, Receipt as RelayReceipt, Vehicle as RelayVehicle  # noqa: E402

PLATE = "MH12AB1234"


@pytest.fixture()
def edge_db():
    edge_settings.cache_clear()
    assert edge_settings().relay_api_key == KEY
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    EdgeBase.metadata.create_all(eng)
    set_engine(eng)
    s = EdgeSession()
    seed_reference(s)
    seed_users(s)
    s.commit()
    set_gateway(EdgeMockGateway())
    set_messenger(Messenger(sms=NoOpSender("SMS"), whatsapp=NoOpSender("WHATSAPP")))
    try:
        yield s
    finally:
        s.close()
        eng.dispose()


def _plate_crop() -> str:
    from PIL import Image, ImageDraw

    rel = "it/crop.jpg"
    p = Path(IMAGE_ROOT) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", (400, 120), "white")
    ImageDraw.Draw(im).text((20, 40), PLATE, fill="black")
    im.save(p, "JPEG")
    return rel


def edge_event(db, direction, plate, ts, gate):
    payload = {"event_id": str(uuid.uuid4()), "gate_id": gate, "camera_ids": [f"{gate}-L"], "direction": direction,
               "wrong_way": False, "vehicle_class": "BIKE", "ts_ms": int(ts.timestamp() * 1000), "status": "READ",
               "plate": plate, "confidence": 0.96, "candidates": [{"plate": plate, "confidence": 0.96}],
               "images": {"plate_crop": _plate_crop()}, "latency_ms": 400}
    res = ingest_event(db, payload)
    db.commit()
    return res


class InProcessRelayClient(edge.RelayClient):
    """The edge's own RelayClient, with its httpx client pointed at the relay ASGI app."""

    def __init__(self, relay_app):  # noqa: D107 - deliberately not calling super().__init__
        self.client = TestClient(relay_app, base_url="https://testserver", headers={"X-Relay-Key": KEY})


def relay_db(relay_app):
    return relay_app.state.sessionmaker()


def test_self_pay_on_relay_becomes_confirmed_edge_payment_and_receipt_renders(edge_db):
    db = edge_db
    relay_app = create_app(make_settings())
    rc = InProcessRelayClient(relay_app)
    customer = TestClient(relay_app, base_url="https://testserver")

    # 1. a bike enters at the edge; the first sync is a full push
    res = edge_event(db, "IN", PLATE, utcnow() - timedelta(minutes=30), "G1")
    sid = res.session.id
    out = edge.sync_once(db, rc)
    assert out["applied"] == 0 and out["pushed_entries"] == 1
    rdb = relay_db(relay_app)
    e = rdb.get(Entry, sid)
    assert e.plate == PLATE and e.masked_plate == "MH12••••34" and e.thumb_b64  # blurred thumbnail arrived
    assert e.dues_paise == 0 and e.credit_paise == 0
    thumb = e.thumb_b64
    assert set(e.quotes) == {"120", "240", "480", "720", "1440"}
    rdb.close()

    # 2. the public list shows the masked plate only
    page = customer.get("/pay").text
    assert "MH12••••34" in page and PLATE not in page

    # 3. customer types the plate, picks 4 h, pays with the (mock) gateway on the relay
    r = customer.post("/pay/find", data={"csrf_token": csrf(customer), "plate": "MH 12 AB 1234"})
    reveal = re.search(r'name="reveal" value="([^"]+)"', r.text).group(1)
    r = customer.post("/pay/start", data={"csrf_token": csrf(customer), "sid": sid, "duration": 240,
                                          "reveal": reveal}, follow_redirects=False)
    loc = r.headers["location"]
    customer.post(loc + "/demo-pay", data={"csrf_token": csrf(customer)})
    quote = e.quotes["240"]

    # 4. next edge sync pulls SELF_PAY_PAID, confirms it, and pushes the new receipt in the same cycle
    out = edge.sync_once(db, rc)
    assert out["applied"] == 1
    pay = db.scalars(select(Payment).where(Payment.channel == "SELF_PAY")).one()
    assert pay.status == "CONFIRMED" and pay.purpose == "SESSION" and pay.mode == "UPI"
    assert pay.session_id == sid and pay.amount_paise == quote and pay.duration_minutes == 240
    assert pay.txn_ref.startswith("PS") and pay.utr and pay.gateway_ref
    assert pay.phone is None  # no OTP on the relay -> phone_verified false -> nothing attached
    assert db.get(ParkingSession, sid).status == "PREPAID"
    assert pay.receipt_id is not None

    rdb = relay_db(relay_app)
    assert rdb.scalars(select(Outbox)).one().acked_at is not None  # acked by the edge
    rec = rdb.scalars(select(RelayReceipt)).one()
    assert rec.txn_ref == pay.txn_ref
    e = rdb.get(Entry, sid)
    assert e.status == "PREPAID" and e.thumb_b64 == thumb  # delta push kept the thumbnail
    rdb.close()

    # 5. the customer's success page now links the edge-issued receipt, which renders on the relay
    st = customer.get(loc + "/status").json()
    assert st["status"] == "PAID" and st["receipt_url"] == f"/r/{rec.code}"
    page = customer.get(st["receipt_url"])
    assert page.status_code == 200
    for s in ("Station Parking", "MH 12 AB 1234", rec.number, "4 h", f"₹{quote // 100}", pay.utr,
              "Final charge is calculated on actual time; any difference is adjusted on your next visit."):
        assert s in page.text, s

    # 6. re-delivery is harmless: a replayed message is not applied twice
    assert edge.apply_message(db, {"id": rdb_msg_id(relay_app), "kind": "SELF_PAY_PAID",
                                   "payload": {}})["status"] == "already_applied"


def rdb_msg_id(relay_app):
    s = relay_db(relay_app)
    try:
        return s.scalars(select(Outbox.id)).first()
    finally:
        s.close()


def test_pass_contact_dispute_and_data_request_reach_the_edge(edge_db):
    db = edge_db
    relay_app = create_app(make_settings())
    rc = InProcessRelayClient(relay_app)
    customer = TestClient(relay_app, base_url="https://testserver")
    # a known vehicle with dues and no phone on record
    edge_event(db, "IN", PLATE, utcnow() - timedelta(hours=30), "G1")
    edge_event(db, "OUT", PLATE, utcnow() - timedelta(hours=20), "G2")
    veh = db.scalars(select(EdgeVehicle).where(EdgeVehicle.plate == PLATE)).one()
    assert veh.balance_paise > 0 and veh.phone is None
    dues = veh.balance_paise
    edge.sync_once(db, rc)

    # OTP -> balance only (no phone on record) -> pay dues -> DUES_PAID + CONTACT_VERIFIED
    r = login(customer, relay_app, PLATE, "9876543210")
    assert "linked to this vehicle" in r.text and "Recent visits" not in r.text
    r = customer.post("/dues/pay", data={"csrf_token": csrf(customer)}, follow_redirects=False)
    customer.post(r.headers["location"] + "/demo-pay", data={"csrf_token": csrf(customer)})

    # buy the monthly pass pushed by the edge
    bike_monthly = db.scalars(select(EdgePassType).where(EdgePassType.vehicle_class == "BIKE",
                                                         EdgePassType.active.is_(True))).first()
    page = customer.get("/pass/me").text
    assert bike_monthly.name in page and f"₹{bike_monthly.price_paise // 100}" in page
    r = customer.post("/pass/pay", data={"csrf_token": csrf(customer), "pass_type_id": bike_monthly.id},
                      follow_redirects=False)
    customer.post(r.headers["location"] + "/demo-pay", data={"csrf_token": csrf(customer)})

    # a dispute and a DPDP data request
    day = (utcnow() + timedelta(hours=5, minutes=30) - timedelta(days=1)).date().isoformat()
    customer.post("/dues/dispute", data={"csrf_token": csrf(customer), "date": day, "time": "18:30", "amount": "20",
                                         "mode": "CASH"})
    customer.get("/privacy/request")
    customer.post("/privacy/request", data={"csrf_token": csrf(customer), "type": "ACCESS"})

    out = edge.sync_once(db, rc)
    assert out["applied"] == 6  # DUES_PAID, CONTACT_VERIFIED, PASS_PAID, CONTACT_VERIFIED, DISPUTE, DATA_REQUEST
    results = {r.kind: r.result for r in db.scalars(select(RelayInbox)).all()}
    assert set(results) == {"DUES_PAID", "CONTACT_VERIFIED", "PASS_PAID", "DISPUTE", "DATA_REQUEST"}
    assert all("error" not in (v or {}) for v in results.values()), results
    db.expire_all()
    veh = db.get(EdgeVehicle, veh.id)
    assert veh.phone == "9876543210" and veh.balance_paise == 0
    dues_pay = db.scalars(select(Payment).where(Payment.purpose == "DUES")).one()
    assert dues_pay.status == "CONFIRMED" and dues_pay.amount_paise == dues and dues_pay.txn_ref.startswith("PRD")
    assert dues_pay.phone == "9876543210"  # OTP-verified on the relay (phone_verified: true)
    p = db.scalars(select(Pass).where(Pass.vehicle_id == veh.id)).one()
    assert p.status == "ACTIVE" and p.channel == "SELF_PAY"
    d = db.scalars(select(PaymentDispute)).one()
    assert d.raised_by_role == "CUSTOMER" and d.claimed_paise == 2000 and d.claimed_when.startswith(day)
    assert d.session_id is not None
    assert db.scalars(select(Alert).where(Alert.kind == "DATA_REQUEST")).one()

    # the next push carries the phone hash + active pass: the customer now sees full history on the relay
    edge.sync_once(db, rc)
    rdb = relay_db(relay_app)
    rv = rdb.get(RelayVehicle, PLATE)
    assert rv.phone_hash and rv.pass_["status"] == "ACTIVE" and rv.balance_paise == 0
    assert rdb.scalars(select(Outbox).where(Outbox.acked_at.is_(None))).all() == []
    assert len(rdb.scalars(select(RelayReceipt)).all()) == 2  # dues + pass receipts
    rdb.close()
    page = customer.get("/dues/me").text
    assert "Recent visits" in page and "You have no dues" in page and "Current pass valid till" in page
