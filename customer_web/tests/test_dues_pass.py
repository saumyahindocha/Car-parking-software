from .conftest import csrf, demo_pay, entry, login, pull, push, vehicle

HIST = [{"entry_at": "2026-09-20T03:30:00+00:00", "exit_at": "2026-09-20T12:00:00+00:00", "charge_paise": 3000,
         "status": "CLOSED"}]


def pay_dues(client):
    r = client.post("/dues/pay", data={"csrf_token": csrf(client)}, follow_redirects=False)
    assert r.status_code == 303, r.text
    return r.headers["location"]


def test_dues_matching_phone_sees_balance_and_history_and_pays(client, app):
    push(client, vehicles=[vehicle("MH12AB1234", 4500, phone="9876543210", history=HIST)])
    r = login(client, app, "MH12AB1234", "9876543210")
    assert "₹45" in r.text and "Recent visits" in r.text and "₹30" in r.text
    loc = pay_dues(client)
    assert "₹45" in client.get(loc).text
    demo_pay(client, loc)
    msgs = pull(client)["messages"]
    assert [m["kind"] for m in msgs] == ["DUES_PAID"]  # phone already on record: no CONTACT_VERIFIED
    p = msgs[0]["payload"]
    assert p["plate"] == "MH12AB1234" and p["amount_paise"] == 4500 and p["dues_paise"] == 4500
    assert p["phone"] == "9876543210" and p["txn_ref"].startswith("PRD") and p["paid_at"]
    assert p["phone_verified"] is True
    assert set(p) == {"plate", "amount_paise", "dues_paise", "txn_ref", "gateway_ref", "utr", "phone",
                      "phone_verified", "paid_at"}
    # until the edge pushes the new balance, the page shows it as pending and offers no second payment
    r = client.get("/dues/me")
    assert "reflected shortly" in r.text and "Pay dues" not in r.text


def test_dues_no_phone_on_record_balance_only_then_contact_verified(client, app):
    push(client, vehicles=[vehicle("MH12AB1234", 2000, phone=None, history=HIST)])
    r = login(client, app, "MH12AB1234", "9876543210")
    assert "₹20" in r.text and "Recent visits" not in r.text and "linked to this vehicle" in r.text
    demo_pay(client, pay_dues(client))
    msgs = pull(client)["messages"]
    assert [m["kind"] for m in msgs] == ["DUES_PAID", "CONTACT_VERIFIED"]
    assert msgs[1]["payload"] == {"plate": "MH12AB1234", "phone": "9876543210"}


def test_dues_phone_mismatch_hides_balance(client, app):
    push(client, vehicles=[vehicle("MH12AB1234", 2000, phone="9000000001", history=HIST)])
    r = login(client, app, "MH12AB1234", "9876543210")
    assert "not registered" in r.text and "₹20" not in r.text
    assert client.post("/dues/pay", data={"csrf_token": csrf(client)}).status_code == 403
    assert client.get("/dues/dispute").status_code == 403


def test_unknown_vehicle(client, app):
    r = login(client, app, "MH12ZZ0001", "9876543210")
    assert "no record" in r.text


def test_dispute_message(client, app):
    push(client, vehicles=[vehicle("MH12AB1234", 3000, phone="9876543210")])
    login(client, app, "MH12AB1234", "9876543210")
    assert "I paid cash" in client.get("/dues/me").text
    form = client.get("/dues/dispute")
    assert form.status_code == 200 and 'value="30"' in form.text
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    day = (datetime.now(ZoneInfo("Asia/Kolkata")) - timedelta(days=1)).date().isoformat()
    r = client.post("/dues/dispute", data={"csrf_token": csrf(client), "date": day, "time": "18:40", "amount": "30",
                                           "mode": "CASH", "note": "Paid the attendant near gate 2"})
    assert r.status_code == 200 and "recorded" in r.text
    [m] = pull(client)["messages"]
    assert m["kind"] == "DISPUTE"
    assert m["payload"] == {"plate": "MH12AB1234", "claimed_paise": 3000, "claimed_mode": "CASH",
                            "claimed_when": f"{day}T18:40+05:30", "note": "Paid the attendant near gate 2"}
    bad = client.post("/dues/dispute", data={"csrf_token": csrf(client), "date": "2020-01-01", "time": "10:00"})
    assert bad.status_code == 400


def test_pass_purchase(client, app):
    push(client, [entry(41, "MH12AB1234")], [vehicle("MH12AB1234", phone=None,
                                                      pass_={"pass_type": "Monthly", "ends_on": "2026-09-30"})])
    r = client.get("/pass?plate=mh12ab1234")
    assert 'value="MH12AB1234"' in r.text  # prefilled from the reminder link
    r = login(client, app, "MH12AB1234", "9876543210", nxt="pass")
    assert r.url.path == "/pass/me"
    assert "Monthly" in r.text and "₹600" in r.text and "Monthly car" not in r.text  # only the vehicle's class
    assert "2026-09-30" in r.text
    r = client.post("/pass/pay", data={"csrf_token": csrf(client), "pass_type_id": 2})
    assert r.status_code == 400  # car pass for a bike
    r = client.post("/pass/pay", data={"csrf_token": csrf(client), "pass_type_id": 1}, follow_redirects=False)
    loc = r.headers["location"]
    done = demo_pay(client, loc)
    assert "activates as soon as the payment reaches" in done.text
    msgs = pull(client)["messages"]
    assert [m["kind"] for m in msgs] == ["PASS_PAID", "CONTACT_VERIFIED"]
    p = msgs[0]["payload"]
    assert p["plate"] == "MH12AB1234" and p["vehicle_class"] == "BIKE" and p["pass_type_id"] == 1
    assert p["amount_paise"] == 60000 and p["txn_ref"].startswith("PRP") and p["phone"] == "9876543210"
    assert p["phone_verified"] is True
    assert set(p) == {"plate", "vehicle_class", "pass_type_id", "amount_paise", "txn_ref", "gateway_ref", "utr",
                      "phone", "phone_verified", "paid_at"}


def test_pass_for_unknown_vehicle_lists_all_classes(client, app):
    push(client)
    r = login(client, app, "MH12NEW001", "9876543210", nxt="pass")
    assert "Monthly car" in r.text and "Monthly" in r.text


def test_privacy_notice_and_data_request(client, app):
    r = client.get("/privacy")
    assert "Digital Personal Data Protection Act, 2023" in r.text and "90 days" in r.text
    assert client.get("/privacy/request", follow_redirects=False).headers["location"] == "/verify?next=privacy"
    login(client, app, "MH12AB1234", "9876543210", nxt="privacy")
    r = client.post("/privacy/request", data={"csrf_token": csrf(client), "type": "DELETE", "note": "sold the bike"})
    assert "30 days" in r.text
    [m] = pull(client)["messages"]
    assert m["kind"] == "DATA_REQUEST" and m["payload"]["request_type"] == "DELETE"
    from .conftest import H

    dr = client.get("/sync/data-requests", headers=H).json()
    assert dr[0]["payload"]["plate"] == "MH12AB1234"


def test_logout(client, app):
    login(client, app, "MH12AB1234", "9876543210")
    client.get("/logout")
    assert client.get("/dues/me", follow_redirects=False).status_code == 303
