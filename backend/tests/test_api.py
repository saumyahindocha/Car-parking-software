import time

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings


@pytest.fixture()
def client(engine, gateway, messenger):
    from app.main import app

    get_settings().demo_mode = True
    with TestClient(app) as c:
        yield c


def login(c, username, pin=None, password=None, device_id=None):
    r = c.post("/api/auth/login", json={"username": username, "pin": pin, "password": password, "device_id": device_id})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def anpr(c, direction, plate, gate):
    import uuid

    body = {"event_id": str(uuid.uuid4()), "gate_id": gate, "camera_ids": [f"{gate}-L", f"{gate}-R"], "direction": direction,
            "vehicle_class": "BIKE", "ts_ms": int(time.time() * 1000), "status": "READ", "plate": plate, "confidence": 0.97,
            "candidates": [{"plate": plate, "confidence": 0.97}], "images": {"plate_crop": "a.jpg"}, "latency_ms": 700}
    r = c.post("/api/anpr/events", json=body, headers={"X-Device-Key": get_settings().anpr_api_key})
    assert r.status_code == 200, r.text
    r2 = c.post("/api/anpr/events", json=body, headers={"X-Device-Key": get_settings().anpr_api_key})
    assert r2.json()["duplicate_delivery"] is True
    return r.json()


def test_full_worker_flow(client):
    c = client
    assert c.post("/api/anpr/events", json={}, headers={"X-Device-Key": "wrong"}).status_code == 401
    get_settings().demo_mode = False  # production: worker accounts are bound to one phone
    w = login(c, "w1", pin="1111", device_id="phone-1")
    assert c.post("/api/auth/login", json={"username": "w1", "pin": "1111", "device_id": "phone-2"}).status_code == 403
    get_settings().demo_mode = True
    boot = c.get("/api/bootstrap", headers=w).json()
    assert boot["tariffs"] and boot["settings"]["duration_buttons"]
    ev = anpr(c, "IN", "MH43AB1234", "G1")
    assert ev["anpr_status"] == "MATCHED"
    lst = c.get("/api/collect/list", headers=w).json()
    assert lst and lst[0]["plate"] == "MH43AB1234"
    sid = lst[0]["session_id"]
    q = c.get(f"/api/sessions/{sid}/quote?duration_minutes=240", headers=w).json()
    assert q["amount_paise"] == 2000
    # UPI -> mock pay -> confirmed
    p = c.post("/api/payments/upi", json={"session_id": sid, "duration_minutes": 240, "expected_amount_paise": 2000},
               headers=w).json()
    assert p["status"] == "INITIATED" and p["upi_uri"]
    c.post(f"/api/demo/pay/{p['id']}")
    p2 = c.get(f"/api/payments/{p['id']}", headers=w).json()
    assert p2["status"] == "CONFIRMED" and p2["receipt"]["channel"] == "QR"
    assert c.post(f"/api/receipts/{p2['receipt']['code']}/shown", headers=w).json()["delivery_status"] == "SHOWN"
    assert c.get(f"/api/public/receipts/{p2['receipt']['code']}").json()["amount_paise"] == 2000
    assert c.get(f"/r/{p2['receipt']['code']}").status_code == 200
    # cash on a second vehicle
    anpr(c, "IN", "MH43AB5555", "G1")
    sid2 = [x for x in c.get("/api/collect/list", headers=w).json() if x["plate"] == "MH43AB5555"][0]["session_id"]
    r = c.post("/api/payments/cash", json={"session_id": sid2, "duration_minutes": 120, "phone": "98765 43210"}, headers=w)
    assert r.status_code == 200 and r.json()["cash"]["cash_in_hand_paise"] == 1000
    # exit
    out = anpr(c, "OUT", "MH43AB1234", "G2")
    assert out["anpr_status"] == "MATCHED"
    # search with confusion
    s = c.get("/api/vehicles/search?q=MH43A81234", headers=w).json()
    assert s[0]["plate"] == "MH43AB1234"
    # supervisor sees holdings, confirms handover
    sup = login(c, "sup1", password="super123")
    assert c.get("/api/cash/holdings", headers=sup).json()[0]["cash_in_hand_paise"] == 1000
    ho = c.post("/api/cash/handovers", json={"amount_paise": 1000, "denominations": {"10": 1}}, headers=w).json()
    r = c.post(f"/api/cash/handovers/{ho['id']}/confirm", data={"counted_denominations": '{"10": 1}'},
               files={"photo": ("cash.jpg", b"\xff\xd8fake", "image/jpeg")}, headers=sup)
    assert r.status_code == 200, r.text
    assert c.get("/api/me/cash", headers=w).json()["cash_in_hand_paise"] == 0
    # worker cannot see audit / reports
    assert c.get("/api/audit", headers=w).status_code == 403
    assert c.get("/api/reports/revenue", headers=sup).status_code == 200
    assert c.get("/api/reports/worker-comparison?format=xlsx", headers=sup).status_code == 200
    assert c.get("/api/reports/revenue?format=pdf", headers=sup).status_code == 200
    adm = login(c, "admin", password="admin123")
    assert c.get("/api/audit?table=payments", headers=adm).json()
    live = c.get("/api/dashboard/live", headers=sup).json()
    assert "occupancy" in live and live["latest_events"]
    assert c.get("/api/review", headers=sup).status_code == 200


def test_offline_sync_batch(client):
    c = client
    w = login(c, "w2", pin="2222")
    anpr(c, "IN", "KA01AB0001", "G1")
    sid = c.get("/api/collect/list", headers=w).json()[0]["session_id"]
    items = [{"type": "CASH", "client_uuid": "u-1", "created_at": "2026-09-26T10:00:00+05:30",
              "data": {"session_id": sid, "duration_minutes": 120, "amount_paise": 1000, "dues_paise": 0}},
             {"type": "CASH", "client_uuid": "u-1", "created_at": "2026-09-26T10:00:00+05:30",
              "data": {"session_id": sid, "duration_minutes": 120, "amount_paise": 1000, "dues_paise": 0}},
             {"type": "CASH", "client_uuid": "u-2", "created_at": "2026-09-26T10:01:00+05:30",
              "data": {"session_id": sid, "duration_minutes": 120, "amount_paise": 99900, "dues_paise": 0}}]
    res = c.post("/api/sync", json={"items": items}, headers=w).json()["results"]
    assert res[0]["ok"] and res[1]["ok"] and res[0]["payment"]["id"] == res[1]["payment"]["id"]
    assert not res[2]["ok"]


def test_websocket_exit_to_alert_unit(client):
    c = client
    key = get_settings().device_api_key
    with c.websocket_connect(f"/ws/device?key={key}&gate_id=G2") as ws:
        anpr(c, "IN", "MH12ZZ0001", "G1")
        anpr(c, "OUT", "MH12ZZ0001", "G2")
        msg = ws.receive_json()
        assert msg["topic"] == "exit" and msg["data"]["state"] == "RED" and msg["data"]["plate"] == "MH12ZZ0001"


def test_sync_extensions_cancel_and_claim_existing(client, gateway):
    c = client
    w = login(c, "w3", pin="3333")
    anpr(c, "IN", "KA01AB0002", "G1")
    sid = c.get("/api/collect/list", headers=w).json()[0]["session_id"]
    # UPI QR abandoned -> cancel
    p = c.post("/api/payments/upi", json={"session_id": sid, "duration_minutes": 120}, headers=w).json()
    assert c.post(f"/api/payments/{p['id']}/cancel", json={}, headers=w).json()["status"] == "FAILED"
    # gateway down: server makes an offline QR, phone then loses the server and syncs the claim
    gateway.online = False
    p2 = c.post("/api/payments/upi", json={"session_id": sid, "duration_minutes": 120}, headers=w).json()
    assert p2["offline"]
    items = [{"type": "UPI_CLAIM", "client_uuid": "c-9", "created_at": "2026-09-26T10:00:00+05:30",
              "data": {"session_id": sid, "duration_minutes": 120, "amount_paise": 1000, "txn_ref": p2["txn_ref"]}},
             {"type": "SHIFT_OPEN", "client_uuid": "s-1", "created_at": "2026-09-26T09:00:00+05:30", "data": {}},
             {"type": "CASH", "client_uuid": "bad-1", "created_at": "2026-09-26T10:01:00+05:30",
              "data": {"session_id": 999999, "duration_minutes": 120, "amount_paise": 1000}}]
    res = c.post("/api/sync", json={"items": items}, headers=w).json()["results"]
    assert res[0]["ok"] and res[0]["payment"]["id"] == p2["id"] and res[0]["payment"]["status"] == "CLAIMED_OFFLINE"
    assert res[1]["ok"] and not res[2]["ok"]
    sup = login(c, "sup1", password="super123")
    assert any(a["kind"] == "SYNC_FAILED" for a in c.get("/api/alerts?kind=SYNC_FAILED", headers=sup).json())
    # offline cash with a phone-generated receipt code
    anpr(c, "IN", "KA01AB0003", "G1")
    sid2 = [x for x in c.get("/api/collect/list", headers=w).json() if x["plate"] == "KA01AB0003"][0]["session_id"]
    res = c.post("/api/sync", json={"items": [{"type": "CASH", "client_uuid": "c-10", "created_at": "2026-09-26T10:05:00+05:30",
                 "data": {"session_id": sid2, "duration_minutes": 120, "amount_paise": 1000, "receipt_code": "abcd2345",
                          "phone": "+91 98765-43210"}}]}, headers=w).json()["results"]
    assert res[0]["payment"]["receipt"]["code"] == "abcd2345"
    assert c.get("/api/public/receipts/abcd2345").status_code == 200
    assert c.get("/api/bootstrap", headers=w).json()["site_timezone"] == "Asia/Kolkata"


def test_customer_import_endpoints(client):
    c = client
    adm = login(c, "admin", password="admin123")
    sup = login(c, "sup1", password="super123")
    assert c.get("/api/import/customers/template", headers=sup).status_code == 403
    tpl = c.get("/api/import/customers/template", headers=adm)
    assert tpl.status_code == 200 and tpl.text.startswith("plate,")
    good = b"plate,phone,opening_balance\nMH43AB1234,9876543210,40\n"
    bad = good + b"NOTAPLATE,,\n"
    r = c.post("/api/import/customers", files={"file": ("c.csv", good, "text/csv")}, headers=adm).json()
    assert r["committed"] is False and r["summary"]["new_vehicles"] == 1
    assert c.get("/api/vehicles/search?q=MH43AB1234", headers=adm).json() == []          # dry run changed nothing
    assert c.post("/api/import/customers?commit=true", files={"file": ("c.csv", bad, "text/csv")},
                  headers=adm).status_code == 400
    r = c.post("/api/import/customers?commit=true&skip_errors=true", files={"file": ("c.csv", bad, "text/csv")},
               headers=adm).json()
    assert r["committed"] and r["imported_rows"] == 1
    v = c.get("/api/vehicles/search?q=MH43AB1234", headers=adm).json()[0]
    assert v["balance_paise"] == 4000 and v["phone"] == "9876543210"
    assert c.post("/api/import/customers", files={"file": ("c.pdf", b"x", "application/pdf")}, headers=adm).status_code == 400
