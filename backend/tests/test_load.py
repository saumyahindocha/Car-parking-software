"""Spec 14: bursts of 10 events/s across all cameras must be absorbed without backlog."""
import time

from fastapi.testclient import TestClient

from app.config import get_settings
from loadtest.burst import run


def test_burst_10_events_per_second(engine, gateway, messenger):
    from app.main import app

    client = TestClient(app)
    key = get_settings().anpr_api_key

    def post(ev):
        t = time.monotonic()
        r = client.post("/api/anpr/events", json=ev, headers={"X-Device-Key": key})
        return r.status_code == 200, time.monotonic() - t

    res = run(post, rate=10, seconds=3, workers=1)
    assert res["errors"] == 0 and res["kept_up"]
    assert res["p99_ms"] < 500
