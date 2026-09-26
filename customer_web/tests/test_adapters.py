import hashlib
import hmac
import json

import httpx
import pytest

from relay.gateway import GatewayUnavailable, RazorpayGateway
from relay.sms import Msg91Sms

from .conftest import csrf, entry, make_settings, pull, push


def rzp(handler):
    client = httpx.Client(base_url=RazorpayGateway.base, transport=httpx.MockTransport(handler))
    return RazorpayGateway("rzp_key", "rzp_secret", "whsec", client=client)


def test_razorpay_create_qr_and_status():
    seen = {}

    def handler(req: httpx.Request):
        if req.method == "POST":
            seen["body"] = json.loads(req.content)
            return httpx.Response(200, json={"id": "qr_ABC", "image_url": "https://rzp.io/i/x",
                                             "image_content": "upi://pay?pa=x@rzp&am=20.00&tr=PS1XABCDEF"})
        return httpx.Response(200, json={"items": [{"id": "pay_1", "status": "captured", "amount": 2000,
                                                    "acquirer_data": {"rrn": "412345678901"}}]})

    gw = rzp(handler)
    qr = gw.create_upi_qr(2000, "PS1XABCDEF", "Parking MH12••••34")
    assert qr.order_id == "qr_ABC" and qr.upi_uri.startswith("upi://") and qr.image_url
    b = seen["body"]
    assert b["type"] == "upi_qr" and b["usage"] == "single_use" and b["fixed_amount"] is True
    assert b["payment_amount"] == 2000 and b["notes"] == {"txn_ref": "PS1XABCDEF"}
    st = gw.fetch_status("qr_ABC", "PS1XABCDEF")
    assert (st.status, st.gateway_ref, st.utr, st.amount_paise) == ("PAID", "pay_1", "412345678901", 2000)


def test_razorpay_unavailable():
    def handler(req):
        raise httpx.ConnectError("offline")

    with pytest.raises(GatewayUnavailable):
        rzp(handler).create_upi_qr(2000, "PS1XABCDEF", "x")
    with pytest.raises(GatewayUnavailable):
        rzp(lambda r: httpx.Response(503)).fetch_status("qr", "ref")


def razorpay_webhook(txn_ref="PS1XABCDEF", event="qr_code.credited", amount=2000):
    return json.dumps({"event": event, "payload": {
        "payment": {"entity": {"id": "pay_9", "amount": amount, "status": "captured",
                               "acquirer_data": {"rrn": "498765432109"}, "notes": []}},
        "qr_code": {"entity": {"id": "qr_ABC", "notes": {"txn_ref": txn_ref}}}}}).encode()


def test_razorpay_webhook_signature():
    gw = rzp(lambda r: httpx.Response(200, json={}))
    body = razorpay_webhook()
    sig = hmac.new(b"whsec", body, hashlib.sha256).hexdigest()
    [ev] = gw.parse_webhook(body, {"x-razorpay-signature": sig})
    assert (ev.order_id, ev.txn_ref, ev.status, ev.amount_paise, ev.gateway_ref, ev.utr) == \
        ("qr_ABC", "PS1XABCDEF", "PAID", 2000, "pay_9", "498765432109")
    with pytest.raises(PermissionError):
        gw.parse_webhook(body, {"x-razorpay-signature": "0" * 64})
    with pytest.raises(PermissionError):
        gw.parse_webhook(body, {})
    other = json.dumps({"event": "order.paid", "payload": {}}).encode()
    assert gw.parse_webhook(other, {"x-razorpay-signature": hmac.new(b"whsec", other, hashlib.sha256).hexdigest()}) == []


def test_razorpay_end_to_end_through_relay_webhook():
    """App configured for Razorpay: QR created via the API, confirmed by a signed webhook."""
    from fastapi.testclient import TestClient

    from relay.main import create_app

    def handler(req):
        return httpx.Response(200, json={"id": "qr_ABC", "image_url": "https://rzp.io/i/x",
                                         "image_content": "upi://pay?pa=x@rzp&am=20.00"})

    app = create_app(make_settings(demo_mode=False), gateway=rzp(handler))
    with TestClient(app, base_url="https://testserver") as c:
        push(c, [entry(1, "MH12AB1234")])
        r = c.post("/pay/start", data={"csrf_token": csrf(c), "sid": 1, "duration": 120}, follow_redirects=False)
        page = c.get(r.headers["location"])
        assert "Simulate" not in page.text and "DEMO" not in page.text
        from relay.models import PaymentIntent

        db = app.state.sessionmaker()
        txn = db.query(PaymentIntent).one().txn_ref
        db.close()
        body = razorpay_webhook(txn)
        sig = hmac.new(b"whsec", body, hashlib.sha256).hexdigest()
        assert c.post("/webhooks/razorpay", content=body, headers={"x-razorpay-signature": sig}).json()["handled"] == 1
        [m] = pull(c)["messages"]
        assert m["payload"]["utr"] == "498765432109" and m["payload"]["gateway_ref"] == "pay_9"
        assert c.post("/pay/i/x/demo-pay", data={"csrf_token": csrf(c)}).status_code == 404


def test_msg91_sms():
    seen = {}

    def handler(req):
        seen["h"], seen["b"] = req.headers["authkey"], json.loads(req.content)
        return httpx.Response(200, json={"type": "success"})

    sms = Msg91Sms("auth123", "tmpl_otp", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert sms.send_otp("9876543210", "123456")
    assert seen["h"] == "auth123"
    assert seen["b"] == {"template_id": "tmpl_otp", "short_url": "0",
                         "recipients": [{"mobiles": "919876543210", "otp": "123456"}]}
    bad = Msg91Sms("a", "t", client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401))))
    assert not bad.send_otp("9876543210", "1")
    with pytest.raises(ValueError):
        Msg91Sms("", "t")
