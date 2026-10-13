import re

from sqlalchemy import select

from relay.models import OtpCode

from .conftest import csrf, login, make_settings, push, vehicle


def test_otp_flow_sets_auth_and_is_hashed_at_rest(client, app):
    push(client, vehicles=[vehicle("MH12AB1234", 1500, phone="9876543210")])
    tok = csrf(client)
    r = client.post("/otp/send", data={"csrf_token": tok, "plate": "mh 12 ab 1234", "phone": "+91 98765 43210",
                                       "next": "dues"})
    assert r.status_code == 200 and "******3210" in r.text
    code = app.state.sms.last_code("9876543210")
    assert re.fullmatch(r"\d{6}", code)
    db = app.state.sessionmaker()
    row = db.scalars(select(OtpCode)).one()
    assert code not in row.code_hash and "9876543210" not in row.phone_key and len(row.code_hash) == 64
    db.close()
    r = client.post("/otp/verify", data={"csrf_token": tok, "code": code})
    assert r.status_code == 200 and r.url.path == "/dues/me"
    assert client.cookies.get("cw_auth")
    # used codes cannot be replayed
    r = client.post("/otp/verify", data={"csrf_token": tok, "code": code})
    assert r.status_code == 400


def test_wrong_code_attempts_then_locked(client, app):
    tok = csrf(client)
    client.post("/otp/send", data={"csrf_token": tok, "plate": "MH12AB1234", "phone": "9876543210", "next": "dues"})
    code = app.state.sms.last_code()
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        r = client.post("/otp/verify", data={"csrf_token": tok, "code": wrong})
        assert r.status_code == 400 and "Incorrect OTP" in r.text
    r = client.post("/otp/verify", data={"csrf_token": tok, "code": code})
    assert r.status_code == 400 and "Too many wrong attempts" in r.text
    assert not client.cookies.get("cw_auth")


def test_otp_expiry(client, app):
    from datetime import timedelta

    tok = csrf(client)
    client.post("/otp/send", data={"csrf_token": tok, "plate": "MH12AB1234", "phone": "9876543210", "next": "dues"})
    code = app.state.sms.last_code()
    db = app.state.sessionmaker()
    row = db.scalars(select(OtpCode)).one()
    row.expires_at = row.created_at - timedelta(seconds=1)
    db.commit()
    db.close()
    r = client.post("/otp/verify", data={"csrf_token": tok, "code": code})
    assert r.status_code == 400 and "expired" in r.text


def test_rate_limit_per_phone(client):
    tok = csrf(client)
    for _ in range(3):
        r = client.post("/otp/send", data={"csrf_token": tok, "plate": "MH12AB1234", "phone": "9876543210"})
        assert r.status_code == 200
    r = client.post("/otp/send", data={"csrf_token": tok, "plate": "MH12AB1234", "phone": "9876543210"})
    assert r.status_code == 429 and "15 minutes" in r.text
    # another number from the same device is still fine
    assert client.post("/otp/send", data={"csrf_token": tok, "plate": "MH12AB1234",
                                          "phone": "9876500000"}).status_code == 200


def test_rate_limit_per_ip(client):
    tok = csrf(client)
    for i in range(10):
        r = client.post("/otp/send", data={"csrf_token": tok, "plate": "MH12AB1234", "phone": f"98765000{i:02d}"})
        assert r.status_code == 200
    r = client.post("/otp/send", data={"csrf_token": tok, "plate": "MH12AB1234", "phone": "9123456789"})
    assert r.status_code == 429 and "network" in r.text


def test_validation_and_csrf(client):
    tok = csrf(client)
    r = client.post("/otp/send", data={"csrf_token": tok, "plate": "MH12AB1234", "phone": "12345"})
    assert r.status_code == 400 and "10-digit" in r.text
    r = client.post("/otp/send", data={"csrf_token": tok, "plate": "X", "phone": "9876543210"})
    assert r.status_code == 400
    r = client.post("/otp/send", data={"plate": "MH12AB1234", "phone": "9876543210"})
    assert r.status_code == 403
    r = client.post("/otp/send", data={"csrf_token": "forged", "plate": "MH12AB1234", "phone": "9876543210"})
    assert r.status_code == 403


def test_cookies_are_secure(client, app):
    r = client.get("/")
    cookie = r.headers["set-cookie"]
    assert "cw_csrf=" in cookie and "Secure" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert r.headers["content-security-policy"].startswith("default-src 'self'")
    assert r.headers["x-frame-options"] == "DENY"
    r = login(client, app, "MH12AB1234", "9876543210")
    assert any("cw_auth=" in h and "Secure" in h and "HttpOnly" in h
               for h in r.history[0].headers.get_list("set-cookie"))


def test_no_pii_in_logs(client, app, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    login(client, app, "MH12AB1234", "9876543210")
    code = app.state.sms.last_code()
    text = caplog.text
    assert "9876543210" not in text and code not in text


def test_forged_auth_cookie_rejected(app):
    from fastapi.testclient import TestClient

    from relay.security import sign

    c = TestClient(app, base_url="https://testserver")
    c.cookies.set("cw_auth", sign("other-secret", {"ph": "9876543210", "plate": "MH12AB1234"}, 600, "auth"))
    r = c.get("/dues/me", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/verify")
    assert make_settings().otp_ttl_s == 300
