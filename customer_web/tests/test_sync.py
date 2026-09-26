from relay.models import Entry, PassType, Vehicle
from relay.sync import enqueue

from .conftest import H, KEY, entry, pull, push, vehicle


def test_sync_endpoints_require_relay_key(client):
    assert client.post("/sync/push", json={"entries": []}).status_code == 401
    assert client.post("/sync/push", json={"entries": []}, headers={"X-Relay-Key": "wrong"}).status_code == 401
    assert client.get("/sync/pull").status_code == 401
    assert client.post("/sync/ack", json={"ids": []}, headers={"X-Relay-Key": KEY + "x"}).status_code == 401
    assert client.get("/sync/data-requests").status_code == 401


def test_push_upserts_everything(client, app):
    res = push(client, [entry(1, "MH12AB1234"), entry(2, "MH14CD5678")],
               [vehicle("MH12AB1234", 500, pass_={"id": 9, "pass_type": "Monthly", "pass_type_id": 1,
                                                   "ends_on": "2026-10-20", "phone": "9876543210",
                                                   "plate": "MH12AB1234"})],
               [{"code": "AbC23456", "number": "R260926-0000001", "created_at": "2026-09-26T10:00:00+00:00",
                 "data": {"txn_ref": "PS1X0A0B0C", "plate": "MH 12 AB 1234"}}])
    assert res == {"ok": True, "entries": 2, "removed": 0, "vehicles": 1, "receipts": 1}
    db = app.state.sessionmaker()
    e = db.get(Entry, 1)
    assert e.plate == "MH12AB1234" and e.masked_plate == "MH12••••34" and e.quotes["120"] == 2000
    v = db.get(Vehicle, "MH12AB1234")
    assert v.balance_paise == 500 and v.pass_["ends_on"] == "2026-10-20"
    assert "phone" not in v.pass_  # the edge's pass dict carries the owner's phone: never stored on the relay
    assert db.get(PassType, 1).active and db.get(PassType, 2).active
    db.close()
    # pass types missing from a later push are deactivated; settings are merged
    push(client, pass_types=[{"id": 1, "vehicle_class": "BIKE", "name": "Monthly", "period_unit": "MONTH",
                              "period_value": 1, "price_paise": 65000}], settings={"lot_name": "Kalyan Parking"})
    db = app.state.sessionmaker()
    assert db.get(PassType, 1).price_paise == 65000 and not db.get(PassType, 2).active
    db.close()
    assert "Kalyan Parking" in client.get("/").text


def test_delta_push_keeps_thumbnail_and_full_push_removes_closed(client, app):
    push(client, [entry(1, "MH12AB1234", thumb="QUFB"), entry(2, "MH14CD5678", thumb="QkJC")])
    # delta push (full=false): thumbs are null, one session gone from the list -> kept until a full push
    push(client, [entry(1, "MH12AB1234", thumb=None, status="PREPAID")], full=False)
    db = app.state.sessionmaker()
    e1 = db.get(Entry, 1)
    assert e1.thumb_b64 == "QUFB" and e1.status == "PREPAID"
    assert db.get(Entry, 2) is not None
    db.close()
    assert client.get("/t/1.jpg").content == b"AAA"
    # full push: entry 2 is no longer open -> removed
    res = push(client, [entry(1, "MH12AB1234", thumb=None)], full=True)
    assert res["removed"] == 1
    db = app.state.sessionmaker()
    assert db.get(Entry, 2) is None and db.get(Entry, 1).thumb_b64 == "QUFB"
    db.close()
    # a full push with no open sessions clears everything
    assert push(client, [], full=True)["removed"] == 1


def test_pull_ack_round_trip_and_redelivery(client, app):
    db = app.state.sessionmaker()
    m1 = enqueue(db, "DISPUTE", {"plate": "MH12AB1234", "claimed_paise": 2000, "claimed_mode": "CASH",
                                 "claimed_when": "2026-09-25T09:00+05:30", "note": None})
    m2 = enqueue(db, "CONTACT_VERIFIED", {"plate": "MH12AB1234", "phone": "9876543210"})
    db.commit()
    ids = [m1.id, m2.id]
    db.close()
    data = pull(client)
    assert [m["id"] for m in data["messages"]] == ids
    assert data["messages"][0]["kind"] == "DISPUTE" and data["messages"][0]["payload"]["claimed_paise"] == 2000
    assert set(data["messages"][0]) == {"id", "kind", "payload", "created_at"}
    cursor = data["cursor"]
    # the edge applied only the first one (e.g. the second failed): the second is offered again
    r = client.post("/sync/ack", json={"ids": [ids[0]]}, headers=H)
    assert r.json()["acked"] == 1
    again = pull(client, cursor)
    assert [m["id"] for m in again["messages"]] == [ids[1]]
    assert int(again["cursor"]) >= int(cursor)
    client.post("/sync/ack", json={"ids": [ids[1]]}, headers=H)
    assert client.post("/sync/ack", json={"ids": ids}, headers=H).json()["acked"] == 0  # idempotent
    assert pull(client, again["cursor"])["messages"] == []
    st = client.get("/sync/status", headers=H).json()
    assert st["pending_messages"] == 0 and st["last_push_at"] is None


def test_unknown_kind_rejected_by_enqueue(app):
    import pytest

    db = app.state.sessionmaker()
    with pytest.raises(ValueError):
        enqueue(db, "SOMETHING", {})
    db.close()


def test_direct_delivery_to_edge(app):
    import httpx

    from relay.sync import deliver_direct

    seen = {}

    def handler(req):
        seen["url"], seen["key"] = str(req.url), req.headers["x-relay-key"]
        seen["body"] = req.read()
        return httpx.Response(200, json={"results": [{"id": "m1", "status": "applied"}]})

    c = httpx.Client(transport=httpx.MockTransport(handler))
    out = deliver_direct("http://edge:8000/", KEY, [{"id": "m1", "kind": "DISPUTE", "payload": {}}], client=c)
    assert out == [{"id": "m1", "status": "applied"}]
    assert seen["url"] == "http://edge:8000/api/relay/apply" and seen["key"] == KEY

    def down(req):
        raise httpx.ConnectError("no route")

    assert deliver_direct("http://edge:8000", KEY, [{"id": "m1"}], client=httpx.Client(transport=httpx.MockTransport(down))) is None
