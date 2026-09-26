import os
import re
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from relay.config import Settings  # noqa: E402
from relay.main import create_app  # noqa: E402

KEY = "test-relay-key"
H = {"X-Relay-Key": KEY}


def make_settings(**kw):
    base = dict(database_url="sqlite://", relay_api_key=KEY, secret_key="test-secret", demo_mode=True,
                cookie_secure=True, status_poll_min_s=0)
    base.update(kw)
    return Settings(**base)


@pytest.fixture()
def app():
    return create_app(make_settings())


@pytest.fixture()
def client(app):
    with TestClient(app, base_url="https://testserver") as c:
        yield c


def csrf(client):
    if not client.cookies.get("cw_csrf"):
        client.get("/")
    return client.cookies.get("cw_csrf")


def now():
    return datetime.now(timezone.utc)


def entry(sid, plate, minutes_ago=30, quotes=None, thumb="dGh1bWI=", status="OPEN", masked=None):
    from relay.plates import mask

    return {"session_id": sid, "plate": plate, "masked_plate": masked or mask(plate), "vehicle_class": "BIKE",
            "entry_at": (now() - timedelta(minutes=minutes_ago)).isoformat(), "status": status, "gate_id": "G1",
            "quotes": quotes or {"120": 2000, "240": 3000, "1440": 6000}, "thumb_b64": thumb}


def vehicle(plate, balance=0, phone=None, history=None, pass_=None, vclass="BIKE"):
    from relay.security import edge_phone_hash

    return {"plate": plate, "vehicle_class": vclass, "balance_paise": balance,
            "phone_hash": edge_phone_hash(KEY, phone) if phone else None, "pass": pass_, "history": history or []}


PASS_TYPES = [{"id": 1, "vehicle_class": "BIKE", "name": "Monthly", "period_unit": "MONTH", "period_value": 1,
               "price_paise": 60000},
              {"id": 2, "vehicle_class": "CAR", "name": "Monthly car", "period_unit": "MONTH", "period_value": 1,
               "price_paise": 150000}]
SETTINGS = {"lot_name": "Station Parking", "receipt_footer": "Final charge is calculated on actual time; any "
            "difference is adjusted on your next visit.", "duration_buttons": [120, 240, 1440],
            "upi_vpa": "parking@upi", "upi_payee_name": "Station Parking", "gstin": ""}


def push(client, entries=(), vehicles=(), receipts=(), full=True, pass_types=PASS_TYPES, settings=SETTINGS):
    body = {"generated_at": now().isoformat(), "full": full, "entries": list(entries), "vehicles": list(vehicles),
            "receipts": list(receipts), "pass_types": pass_types, "settings": settings}
    r = client.post("/sync/push", json=body, headers=H)
    assert r.status_code == 200, r.text
    return r.json()


def pull(client, after=""):
    r = client.get("/sync/pull", params={"after": after}, headers=H)
    assert r.status_code == 200, r.text
    return r.json()


def login(client, app, plate, phone, nxt="dues"):
    tok = csrf(client)
    r = client.post("/otp/send", data={"csrf_token": tok, "plate": plate, "phone": phone, "next": nxt})
    assert r.status_code == 200, r.text
    code = app.state.sms.last_code(phone)
    r = client.post("/otp/verify", data={"csrf_token": tok, "code": code})
    assert r.status_code == 200, r.text
    return r


def intent_token(location):
    m = re.match(r"^/pay/i/([A-Za-z0-9_-]+)$", location)
    assert m, location
    return m.group(1)


def demo_pay(client, location):
    r = client.post(location + "/demo-pay", data={"csrf_token": csrf(client)})
    assert r.status_code == 200, r.text
    return r
