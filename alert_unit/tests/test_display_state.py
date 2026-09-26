from exit_alert.display_state import GREEN, NEUTRAL, RED, DisplayModel, pass_lines, pretty_date, rupees


def msg(event_id, state="GREEN", plate="MH12AB1234", **kw):
    d = {"gate_id": "G2", "event_id": event_id, "ts": "2026-09-26T10:00:00+00:00", "state": state, "plate": plate,
         "display_plate": "MH 12 AB 1234" if plate else "—", "vehicle_class": "BIKE", "session_id": 1,
         "plate_image": f"/api/images/{event_id}.jpg", "frame_image": None, "amount_due_paise": 0,
         "pass_valid_till": None, "pass_days_left": None}
    d.update(kw)
    return d


def test_rupees_and_dates():
    assert rupees(2000) == "₹20"
    assert rupees(2050) == "₹20.50"
    assert rupees(12345600) == "₹1,23,456"
    assert rupees(None) == "₹0"
    assert rupees(-500) == "₹0"
    assert pretty_date("2026-10-05") == "5 Oct 2026"


def test_pass_text_warns_only_near_expiry():
    assert pass_lines(None, None, 5) == []
    assert pass_lines("2026-10-30", 34, 5) == ["Pass valid till 30 Oct 2026"]
    assert pass_lines("2026-10-01", 5, 5) == ["Pass valid till 1 Oct 2026", "Pass expires in 5 days"]
    assert pass_lines("2026-09-27", 1, 5)[1] == "Pass expires in 1 day - please renew"
    assert pass_lines("2026-09-26", 0, 5)[1].startswith("Pass expires today")
    assert pass_lines("2026-10-01", 5, 3) == ["Pass valid till 1 Oct 2026"]


def test_green_card_thank_you_and_expiry():
    m = DisplayModel(green_hold=6)
    eff = m.on_exit(msg(1), now=100.0)
    assert not eff.buzz and eff.fetch_images == []  # no image fetch for green
    [c] = m.visible(101)
    assert c.headline() == "Thank you" and c.display_plate == "MH 12 AB 1234"
    assert c.lines(5) == ["Drive safely"]
    assert m.light(101) == GREEN
    assert m.visible(106.1) == []


def test_pass_holder_card_lines():
    m = DisplayModel(warn_days=5)
    m.on_exit(msg(1, pass_valid_till="2026-09-29", pass_days_left=3), 0)
    assert m.visible(0.1)[0].lines(5) == ["Pass valid till 29 Sep 2026", "Pass expires in 3 days"]


def test_red_timeout_returns_to_green_and_buzzes_once():
    m = DisplayModel(red_hold=5)
    eff = m.on_exit(msg(7, RED, amount_due_paise=3000), now=10.0)
    assert eff.buzz and eff.fetch_images == ["/api/images/7.jpg"]
    c = m.visible(10)[0]
    assert c.headline() == "Payment due" and c.lines(5)[0] == "Amount due ₹30"
    assert m.light(14.9) == RED
    assert 0 < m.red_remaining(14.0) <= 1.0
    assert m.light(15.01) == GREEN
    # duplicate delivery of the same event: updated in place, no second buzz
    m2 = DisplayModel()
    m2.on_exit(msg(8, RED), 0)
    assert not m2.on_exit(msg(8, RED), 0.5).buzz
    assert len(m2.visible(0.6)) == 1


def test_side_by_side_slots_and_eviction_keeps_red():
    m = DisplayModel(max_slots=2)
    m.on_exit(msg(1, RED, amount_due_paise=2000), 0)
    m.on_exit(msg(2), 0.2)
    assert [c.key for c in m.visible(0.3)] == ["1", "2"]  # both shown, arrival order
    m.on_exit(msg(3), 0.4)  # slots full -> the green one goes, the red alert stays
    assert [c.key for c in m.visible(0.5)] == ["1", "3"]
    m.on_exit(msg(4, RED), 0.6)  # full again: green 3 evicted
    assert [c.key for c in m.visible(0.7)] == ["1", "4"]
    m.on_exit(msg(5, RED), 0.8)  # all red: oldest red evicted
    assert [c.key for c in m.visible(0.9)] == ["4", "5"]


def test_three_slots():
    m = DisplayModel(max_slots=3)
    for i in range(4):
        m.on_exit(msg(i), i * 0.1)
    assert [c.key for c in m.visible(0.5)] == ["1", "2", "3"]


def test_neutral_unread_never_red():
    m = DisplayModel()
    eff = m.on_exit(msg(9, NEUTRAL, plate=None, display_plate="—", amount_due_paise=5000), 0)
    assert not eff.buzz
    c = m.visible(0.1)[0]
    assert c.display_plate == "—" and c.state == NEUTRAL
    assert m.light(0.1) == GREEN
    # unknown state strings degrade to NEUTRAL
    m.on_exit(msg(10, "WEIRD"), 0.2)
    assert m.visible(0.3)[-1].state == NEUTRAL


def test_status_text_offline():
    m = DisplayModel()
    assert m.status_text() == "OFFLINE - reconnecting"
    m.connected = True
    assert m.status_text() is None
    assert m.light(0) == GREEN  # being offline alone never turns the light red
