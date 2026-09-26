"""End-to-end: synthetic clip pair for one gate -> ClassicalRecognizer pipeline ->
cross-camera merge -> images + outbox -> (mock) backend.

The scene has a side-by-side pair (single-line + two-line plates), a bike in
the overlap seen by BOTH cameras, a staggered partly-occluded pair and one
bike with an unreadable plate.  Expect exactly one event per vehicle.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest

from anpr_service.backend import BackendClient
from anpr_service.config import ServiceConfig
from anpr_service.runner import InlineReplayRunner
from anpr_service.synth import camera_entries

START = 1_760_000_000_000
EVENT_KEYS = {"event_id", "gate_id", "camera_ids", "direction", "wrong_way", "vehicle_class", "ts_ms", "status",
              "plate", "confidence", "candidates", "images", "latency_ms"}


def _config(clip_dir: Path, gt: dict[str, Any], work: Path, backend_url: str = "http://backend:8000") -> ServiceConfig:
    return ServiceConfig.model_validate({
        "image_root": str(work / "images"),
        "outbox_path": str(work / "outbox.sqlite"),
        "backend": {"url": backend_url, "api_key": "test-device-key", "timeout_s": 2},
        "recognizer": {"kind": "classical"},
        "gates": [{"id": gt["gate_id"], "name": "Gate 1", "direction": "IN", "cameras": camera_entries(gt, clip_dir)}],
    })


def _check_events(events: list[dict[str, Any]], gt: dict[str, Any], image_root: Path) -> None:
    readable = [v for v in gt["vehicles"] if not v["unreadable"]]
    reads = [e for e in events if e["status"] == "READ"]
    unreads = [e for e in events if e["status"] == "UNREAD"]
    # one event per vehicle, correct plates, nothing duplicated despite the camera overlap
    assert sorted(e["plate"] for e in reads) == sorted(v["plate"] for v in readable)
    assert len(unreads) == sum(1 for v in gt["vehicles"] if v["unreadable"])
    assert len(events) == len(gt["vehicles"])
    by_plate = {e["plate"]: e for e in reads}
    for v in readable:
        e = by_plate[v["plate"]]
        assert set(e) == EVENT_KEYS
        assert abs(e["ts_ms"] - (START + v["cross_ms"])) <= 400, v["plate"]
        if v["direction"] == "IN":
            assert e["direction"] == "IN" and e["wrong_way"] is False
        else:  # riding towards the cameras through an IN-only gate
            assert e["direction"] == "OUT" and e["wrong_way"] is True
        assert e["vehicle_class"] == "BIKE"
        assert 0.6 <= e["confidence"] <= 1.0
        assert e["candidates"][0]["plate"] == v["plate"] and len(e["candidates"]) <= 3
        assert isinstance(e["latency_ms"], int) and e["latency_ms"] >= 0
        if len(v["visible_in"]) == 2:  # the middle bike: merged across both cameras
            assert e["camera_ids"] == ["G1-L", "G1-R"]
            assert len(e["images"]["extra"]) == 2
        for kind in ("plate_crop", "full_frame"):
            rel = e["images"][kind]
            assert rel and rel.startswith(("20", "19")) and f"/G1/{e['event_id']}_{kind}.jpg" in rel
            assert (image_root / rel).stat().st_size > 500
    for e in unreads:
        assert e["plate"] is None and e["images"]["full_frame"]
        assert (image_root / e["images"]["full_frame"]).exists()
    assert any(v["direction"] == "OUT" for v in readable), "scene includes a wrong-way bike"
    side_by_side = [v for v in readable if v["side_by_side"]]
    assert len(side_by_side) >= 4  # pair + staggered pair are covered by the assertions above


def test_inline_replay_end_to_end(synthetic_gate: tuple[Path, dict[str, Any]], tmp_path: Path) -> None:
    clip_dir, gt = synthetic_gate
    received: list[dict[str, Any]] = []
    stored: set[str] = set()
    calls = {"n": 0}

    def backend(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Device-Key"] == "test-device-key"
        assert request.url.path == "/api/anpr/events"
        calls["n"] += 1
        if calls["n"] == 2:  # one transient failure: must be retried, order preserved
            return httpx.Response(503)
        body = json.loads(request.content)
        if body["event_id"] not in stored:
            stored.add(body["event_id"])
            received.append(body)
        return httpx.Response(200, json={"status": "ok"})

    cfg = _config(clip_dir, gt, tmp_path)
    cfg.emitter.retry_initial_s = 0.05
    cfg.emitter.retry_max_s = 0.1
    sink = BackendClient(cfg.backend, transport=httpx.MockTransport(backend))
    result = InlineReplayRunner(cfg, sink=sink, start_epoch_ms=START, drain_timeout_s=5).run()

    assert result.outbox_depth == 0
    assert [e["event_id"] for e in received] == [e["event_id"] for e in result.events]  # in order
    _check_events(received, gt, tmp_path / "images")
    agg = result.aggregation["G1"]
    assert agg["merged_cross_camera"] >= 1
    # timing instrumentation is populated for every ANPR camera
    assert set(result.timings) == {"G1-L", "G1-R"}
    assert result.timings["G1-L"]["detect"]["mean_ms"] > 0


class _Recorder(BaseHTTPRequestHandler):
    events: list[dict[str, Any]] = []
    heartbeats: list[dict[str, Any]] = []
    lock = threading.Lock()

    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        with self.lock:
            if self.path == "/api/anpr/events":
                assert self.headers.get("X-Device-Key") == "test-device-key"
                if all(e["event_id"] != body["event_id"] for e in self.events):
                    self.events.append(body)
            elif self.path == "/api/devices/heartbeat":
                self.heartbeats.append(body)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *args: Any) -> None:
        return


@pytest.mark.slow
def test_multiprocess_replay_end_to_end(synthetic_gate: tuple[Path, dict[str, Any]], tmp_path: Path) -> None:
    """The production process layout (worker per camera, gate aggregator, emitter) against a real HTTP server."""
    from anpr_service.service import Supervisor

    clip_dir, gt = synthetic_gate
    _Recorder.events = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        cfg = _config(clip_dir, gt, tmp_path, f"http://127.0.0.1:{server.server_address[1]}")
        videos = {c["id"]: str(clip_dir / c["file"]) for c in gt["cameras"]}
        sup = Supervisor(cfg, replay_videos=videos, log_level="WARNING", drain_timeout_s=20)
        sup.start_epoch_ms = START
        assert sup.run() == 0
    finally:
        server.shutdown()
    _check_events(_Recorder.events, gt, tmp_path / "images")
