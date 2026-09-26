import re

import pytest

from relay.models import PaymentIntent

from .conftest import H, csrf, demo_pay, entry, login, pull, push, vehicle

TXN_RE = re.compile(r"^PS([0-9A-Z]+)X[0-9A-F]{6}$")


def start(client, sid, duration=120, reveal=""):
    r = client.post("/pay/start", data={"csrf_token": csrf(client), "sid": sid, "duration": duration,
                                        "reveal": reveal}, follow_redirects=False)
    assert r.status_code == 303, r.text
    return r.headers["location"]


def test_public_list_masks_plates(client):
    push(client, [entry(41, "MH12AB1234"), entry(42, "MH14CD5678", minutes_ago=60 * 6)])
    r = client.get("/pay")
    assert "MH12••••34" in r.text and "MH12AB1234" not in r.text and "MH 12 AB 1234" not in r.text
    assert "MH14••••78" not in r.text  # older than recent_hours (4 h)
    assert '<img src="/t/41.jpg"' in r.text
    r = client.get("/pay/s/41")
    assert "MH12••••34" in r.text and "MH12AB1234" not in r.text
    assert "₹20" in r.text and "₹60" in r.text and "1 day" in r.text


def test_search_exact_reveals_full_plate_and_approx_stays_masked(client):
    push(client, [entry(41, "MH12AB1234"), entry(43, "MH12AB1284")])
    r = client.post("/pay/find", data={"csrf_token": csrf(client), "plate": "mh-12 ab 1234"})
    assert r.status_code == 200 and "MH 12 AB 1234" in r.text
    # OCR-confusable / one-edit typo: candidates listed masked only
    r = client.post("/pay/find", data={"csrf_token": csrf(client), "plate": "MH12A81234"})
    assert "Did you mean" in r.text and "MH12••••34" in r.text and "MH12AB1234" not in r.text
    r = client.post("/pay/find", data={"csrf_token": csrf(client), "plate": "KA01ZZ9999"})
    assert "could not find" in r.text


def test_self_pay_with_mock_gateway_queues_correct_message(client):
    push(client, [entry(41, "MH12AB1234", quotes={"120": 2500, "240": 3500})],
         [vehicle("MH12AB1234", balance=500)])
    r = client.post("/pay/find", data={"csrf_token": csrf(client), "plate": "MH12AB1234"})
    reveal = re.search(r'name="reveal" value="([^"]+)"', r.text).group(1)
    loc = start(client, 41, 120, reveal)
    qr = client.get(loc)
    assert "<svg" in qr.text and "upi://pay?" in qr.text and "MH 12 AB 1234" in qr.text and "₹25" in qr.text
    assert "Simulate payment" in qr.text
    assert pull(client)["messages"] == []  # nothing is queued before the gateway confirms
    st = client.get(loc + "/status").json()
    assert st == {"status": "PENDING", "receipt_url": None}
    done = demo_pay(client, loc)
    assert "Payment successful" in done.text and "receipt will appear" in done.text
    [m] = pull(client)["messages"]
    assert m["kind"] == "SELF_PAY_PAID"
    p = m["payload"]
    assert p["plate"] == "MH12AB1234" and p["session_id"] == 41 and p["vehicle_class"] == "BIKE"
    assert p["duration_minutes"] == 120 and p["amount_paise"] == 2500
    assert p["dues_paise"] == 500 and p["base_paise"] == 2000
    assert TXN_RE.match(p["txn_ref"]) and int(TXN_RE.match(p["txn_ref"]).group(1), 36) == 41
    assert p["gateway_ref"].startswith("pay_") and p["utr"] and p["paid_at"]
    assert p["phone"] is None  # no OTP-verified number -> never sent
    assert set(p) == {"plate", "session_id", "vehicle_class", "duration_minutes", "amount_paise", "base_paise",
                      "dues_paise", "txn_ref", "gateway_ref", "utr", "phone", "paid_at"}
    # the vehicle now shows as paid; a second self-pay is refused
    assert "Paid" in client.get("/pay").text
    assert "already paid" in client.get("/pay/s/41").text
    r = client.post("/pay/start", data={"csrf_token": csrf(client), "sid": 41, "duration": 120})
    assert r.status_code == 400
    # paying again (double click on demo) does not queue a second message
    demo_pay(client, loc)
    assert len(pull(client)["messages"]) == 1


def test_self_pay_from_masked_list_stays_masked_and_sends_verified_phone(client, app):
    push(client, [entry(41, "MH12AB1234")], [vehicle("MH12AB1234", phone="9876543210")])
    login(client, app, "MH12AB1234", "9876543210")
    loc = start(client, 41, 240)
    assert "MH12••••34" in client.get(loc).text and "MH 12 AB 1234" not in client.get(loc).text
    demo_pay(client, loc)
    [m] = pull(client)["messages"]
    assert m["payload"]["phone"] == "9876543210" and m["payload"]["amount_paise"] == 3000


def test_webhook_signature_and_idempotency(client, app):
    push(client, [entry(41, "MH12AB1234")])
    loc = start(client, 41)
    gw = app.state.gateway
    db = app.state.sessionmaker()
    intent = db.query(PaymentIntent).one()
    db.close()
    body, headers = gw.webhook_body(gw.pay(intent.txn_ref))
    assert client.post("/webhooks/mock", content=body, headers={"x-mock-signature": "bad"}).status_code == 401
    assert client.post("/webhooks/razorpay", content=body, headers=headers).status_code == 404
    r = client.post("/webhooks/mock", content=body, headers=headers)
    assert r.json() == {"ok": True, "handled": 1}
    client.post("/webhooks/mock", content=body, headers=headers)  # retried webhook
    assert len(pull(client)["messages"]) == 1
    assert client.get(loc + "/status").json()["status"] == "PAID"
    # webhook for a QR that is not ours (e.g. the edge's, on a shared gateway account) is ignored
    from relay.gateway import WebhookEvent

    b2, h2 = gw.webhook_body(WebhookEvent("qr_other", "PS99XABCDEF", "PAID", 100, "pay_x", "1"))
    assert client.post("/webhooks/mock", content=b2, headers=h2).json()["handled"] == 0


def test_status_poll_confirms_without_webhook(client, app):
    push(client, [entry(41, "MH12AB1234")])
    loc = start(client, 41)
    db = app.state.sessionmaker()
    intent = db.query(PaymentIntent).one()
    db.close()
    app.state.gateway.pay(intent.txn_ref)  # customer paid; the webhook never arrives
    assert client.get(loc + "/status").json()["status"] == "PAID"
    assert [m["kind"] for m in pull(client)["messages"]] == ["SELF_PAY_PAID"]


def test_gateway_amount_mismatch_uses_paid_amount(client, app):
    push(client, [entry(41, "MH12AB1234", quotes={"120": 2500})], [vehicle("MH12AB1234", balance=500)])
    loc = start(client, 41)
    db = app.state.sessionmaker()
    intent = db.query(PaymentIntent).one()
    db.close()
    app.state.gateway.pay(intent.txn_ref, amount_paise=300)
    client.get(loc + "/status")
    p = pull(client)["messages"][0]["payload"]
    assert p["amount_paise"] == 300 and p["dues_paise"] == 300 and p["base_paise"] == 0


def test_gateway_offline_shows_error(client, app):
    push(client, [entry(41, "MH12AB1234")])
    app.state.gateway.online = False
    r = client.post("/pay/start", data={"csrf_token": csrf(client), "sid": 41, "duration": 120})
    assert r.status_code == 400 and "temporarily unavailable" in r.text
    assert pull(client)["messages"] == []


@pytest.mark.parametrize("sid,duration", [(999, 120), (41, 17)])
def test_bad_self_pay_requests(client, sid, duration):
    push(client, [entry(41, "MH12AB1234")])
    r = client.post("/pay/start", data={"csrf_token": csrf(client), "sid": sid, "duration": duration})
    assert r.status_code in (400, 404)


def test_expired_qr(client, app):
    from datetime import timedelta

    push(client, [entry(41, "MH12AB1234")])
    loc = start(client, 41)
    db = app.state.sessionmaker()
    intent = db.query(PaymentIntent).one()
    intent.expires_at = intent.created_at - timedelta(minutes=5)
    db.commit()
    db.close()
    r = client.get(loc)
    assert "expired" in r.text


def test_search_rate_limit(client):
    push(client, [entry(41, "MH12AB1234")])
    for _ in range(30):
        client.post("/pay/find", data={"csrf_token": csrf(client), "plate": "KA01ZZ9999"})
    r = client.post("/pay/find", data={"csrf_token": csrf(client), "plate": "MH12AB1234"})
    assert r.status_code == 429
    assert H  # relay key header constant is shared with other tests
