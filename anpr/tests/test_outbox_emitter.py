"""Durable outbox + in-order delivery with retry/backoff against a fake backend."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest

from anpr_service.backend import BackendClient, JsonlSink, SendOutcome, classify_status
from anpr_service.config import BackendConfig
from anpr_service.emitter import Emitter
from anpr_service.heartbeat import HeartbeatThread
from anpr_service.outbox import Outbox


class FakeBackend:
    """httpx MockTransport handler scripted with a list of failure modes."""

    def __init__(self, script: list[Any] | None = None) -> None:
        self.script = list(script or [])
        self.received: list[dict[str, Any]] = []
        self.stored: dict[str, dict[str, Any]] = {}
        self.headers: list[httpx.Headers] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.headers.append(request.headers)
        action = self.script.pop(0) if self.script else 200
        if action == "connect_error":
            raise httpx.ConnectError("backend down", request=request)
        if action == "timeout":
            raise httpx.ReadTimeout("slow", request=request)
        body = json.loads(request.content)
        if action == 200:
            self.received.append(body)
            self.stored.setdefault(body["event_id"], body)  # idempotent on event_id
        return httpx.Response(int(action), json={"ok": action == 200})


class FakeClock:
    def __init__(self) -> None:
        self.ms = 1_000_000

    def __call__(self) -> int:
        return self.ms

    def sleep(self, s: float) -> None:
        self.ms += int(s * 1000) + 1


def _event(i: int) -> dict[str, Any]:
    return {"event_id": str(uuid.uuid4()), "gate_id": "G1", "seq_hint": i, "ts_ms": 1_000_000 + i}


def _client(backend: FakeBackend) -> BackendClient:
    return BackendClient(BackendConfig(url="http://backend:8000", api_key="secret-key"),
                         transport=httpx.MockTransport(backend))


def test_outbox_put_is_idempotent_and_ordered(tmp_path: Path) -> None:
    ob = Outbox(tmp_path / "o.sqlite")
    events = [_event(i) for i in range(5)]
    seqs = [ob.put(e) for e in events]
    assert seqs == sorted(seqs)
    assert ob.put(events[2]) == seqs[2]  # same event_id again -> no duplicate
    assert ob.depth() == 5
    assert ob.head().payload["seq_hint"] == 0  # type: ignore[union-attr]


def test_outbox_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "o.sqlite"
    ob = Outbox(path)
    for i in range(3):
        ob.put(_event(i))
    ob.close()
    ob2 = Outbox(path)
    assert ob2.depth() == 3
    assert [ob2.head().payload["seq_hint"]] == [0]  # type: ignore[union-attr]


def test_delivery_in_order_with_retry_and_backoff(tmp_path: Path) -> None:
    backend = FakeBackend(["connect_error", 503, "timeout", 502, 200, 500, 200, 200])
    clock = FakeClock()
    ob = Outbox(tmp_path / "o.sqlite")
    sent = [_event(i) for i in range(6)]
    for e in sent:
        ob.put(e)
    em = Emitter(ob, _client(backend), retry_initial_s=1.0, retry_max_s=8.0, clock_ms=clock, sleep=clock.sleep,
                 jitter=False)
    delivered = em.drain(deadline_s=5.0)
    # wall-clock deadline is real time; the fake clock advances through sleeps
    while ob.depth():
        em.drain(deadline_s=1.0)
    assert delivered <= 6
    assert [e["seq_hint"] for e in backend.received] == [0, 1, 2, 3, 4, 5]
    assert ob.depth() == 0
    assert all(h["X-Device-Key"] == "secret-key" for h in backend.headers)


def test_backoff_grows_exponentially_and_head_is_not_skipped(tmp_path: Path) -> None:
    backend = FakeBackend([503] * 6)
    clock = FakeClock()
    ob = Outbox(tmp_path / "o.sqlite")
    first, second = _event(0), _event(1)
    ob.put(first)
    ob.put(second)
    em = Emitter(ob, _client(backend), retry_initial_s=1.0, retry_max_s=8.0, clock_ms=clock, sleep=clock.sleep,
                 jitter=False)
    delays = []
    for _ in range(5):
        assert em.deliver_once() is False
        head = ob.head()
        assert head is not None and head.event_id == first["event_id"]  # never skips ahead
        delays.append(head.next_attempt_ms - clock.ms)
        assert em.deliver_once() is False  # still backing off: no request made
        clock.ms = head.next_attempt_ms
    assert delays == [1000, 2000, 4000, 8000, 8000]
    assert len(backend.headers) == 5
    backend.script = []  # backend recovers
    assert em.drain(deadline_s=1.0) == 2
    assert [e["seq_hint"] for e in backend.received] == [0, 1]


def test_backend_restart_redelivery_is_idempotent(tmp_path: Path) -> None:
    # Backend stores the event but the response is lost (timeout) -> re-sent; backend dedupes on event_id.
    backend = FakeBackend()
    ob = Outbox(tmp_path / "o.sqlite")
    ev = _event(0)
    ob.put(ev)

    class LossyClient(BackendClient):
        calls = 0

        def send(self, payload):  # type: ignore[no-untyped-def]
            LossyClient.calls += 1
            outcome, info = super().send(payload)
            if LossyClient.calls == 1:
                return SendOutcome.RETRY, "response lost"
            return outcome, info

    clock = FakeClock()
    client = LossyClient(BackendConfig(url="http://b", api_key="k"), transport=httpx.MockTransport(backend))
    em = Emitter(ob, client, 0.01, 0.01, clock_ms=clock, sleep=clock.sleep, jitter=False)
    em.drain(deadline_s=2.0)
    assert ob.depth() == 0
    assert len(backend.received) == 2 and len(backend.stored) == 1


def test_permanent_rejection_goes_to_dead_letter_and_queue_continues(tmp_path: Path) -> None:
    backend = FakeBackend([422, 200])
    ob = Outbox(tmp_path / "o.sqlite")
    ob.put(_event(0))
    ob.put(_event(1))
    em = Emitter(ob, _client(backend), jitter=False)
    assert em.drain(deadline_s=1.0) == 2
    assert [e["seq_hint"] for e in backend.received] == [1]
    assert len(ob.dead_letters()) == 1


@pytest.mark.parametrize(
    "code,outcome",
    [(200, SendOutcome.OK), (201, SendOutcome.OK), (409, SendOutcome.OK), (500, SendOutcome.RETRY),
     (503, SendOutcome.RETRY), (429, SendOutcome.RETRY), (401, SendOutcome.RETRY), (400, SendOutcome.REJECT),
     (422, SendOutcome.REJECT)],
)
def test_status_classification(code: int, outcome: SendOutcome) -> None:
    assert classify_status(code) == outcome


def test_jsonl_sink(tmp_path: Path) -> None:
    ob = Outbox(tmp_path / "o.sqlite")
    ob.put(_event(0))
    em = Emitter(ob, JsonlSink(tmp_path / "events.jsonl"))
    assert em.drain() == 1
    lines = (tmp_path / "events.jsonl").read_text().splitlines()
    assert json.loads(lines[0])["seq_hint"] == 0


def test_heartbeat_payload() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/devices/heartbeat"
        assert request.headers["X-Device-Key"] == "k"
        seen.append(json.loads(request.content))
        return httpx.Response(200)

    client = BackendClient(BackendConfig(url="http://b", api_key="k"), transport=httpx.MockTransport(handler))
    hb = HeartbeatThread(client, "G1-L", "G1", lambda: {"fps": 25.0, "last_frame_ts_ms": 1, "read_rate": 0.97,
                                                        "stream_ok": True, "queue_depth": 0})
    assert hb.beat() is True
    assert seen == [{"device_id": "G1-L", "kind": "CAMERA", "gate_id": "G1",
                     "metrics": {"fps": 25.0, "last_frame_ts_ms": 1, "read_rate": 0.97, "stream_ok": True,
                                 "queue_depth": 0}}]
