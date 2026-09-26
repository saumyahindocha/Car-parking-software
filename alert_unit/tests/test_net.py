import io
import json
import random
import urllib.error

from exit_alert.net import Backoff, Heartbeat, ImageCache, WsLink, heartbeat_body, post_heartbeat


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class FakeWs:
    """Scripted websocket: each recv() pops the next item; exceptions are raised; clock advances."""

    def __init__(self, script, clock, step=1.0):
        self.script = list(script)
        self.clock = clock
        self.step = step
        self.sent = []
        self.closed = False
        self.timeout = None

    def settimeout(self, t):
        self.timeout = t

    def send(self, data):
        self.sent.append(data)

    def recv(self):
        self.clock.t += self.step
        if not self.script:
            raise ConnectionError("script exhausted")
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self):
        self.closed = True


def exit_frame(i, state="GREEN"):
    return json.dumps({"topic": "exit", "data": {"gate_id": "G2", "event_id": i, "state": state}, "ts": "x"})


def test_backoff_grows_caps_and_resets():
    b = Backoff(initial=1, maximum=30, factor=2, jitter=0)
    assert [b.next() for _ in range(7)] == [1, 2, 4, 8, 16, 30, 30]
    b.reset()
    assert b.next() == 1
    j = Backoff(initial=10, maximum=30, jitter=0.2, rng=random.Random(1))
    for _ in range(20):
        assert 0 <= j.next() <= 30


def test_messages_dispatched_ping_sent_and_status():
    clock = FakeClock()
    exits, status = [], []
    ws = FakeWs([exit_frame(1), '{"topic":"pong"}', TimeoutError(), "not json", exit_frame(2),
                 json.dumps({"topic": "other", "data": {}}), ""], clock)
    link = WsLink("ws://x/ws/device", exits.append, status.append, connect=lambda url, t: ws, ping_interval=2.0,
                  clock=clock)
    try:
        link.run_once()
    except ConnectionError:
        pass
    assert [e["event_id"] for e in exits] == [1, 2]
    assert status == [True]
    assert "ping" in ws.sent and ws.closed and ws.timeout == 1.0


def test_silent_socket_is_declared_dead():
    clock = FakeClock()
    ws = FakeWs([TimeoutError()] * 100, clock, step=1.0)
    link = WsLink("ws://x", lambda d: None, lambda s: None, connect=lambda u, t: ws, ping_interval=2.0, clock=clock)
    try:
        link.run_once()
        raise AssertionError("expected TimeoutError")
    except TimeoutError as exc:
        assert "silent" in str(exc)
    assert ws.sent.count("ping") >= 2  # it kept pinging before giving up


def test_run_reconnects_with_backoff_and_resets_after_success():
    clock = FakeClock()
    status, delays, exits = [], [], []
    attempts = {"n": 0}

    def connect(url, timeout):
        attempts["n"] += 1
        if attempts["n"] <= 3:
            raise ConnectionRefusedError("edge down")
        if attempts["n"] == 4:
            return FakeWs([exit_frame(1), ConnectionError("dropped")], clock)
        link.stop()
        raise ConnectionRefusedError("down again")

    link = WsLink("ws://x", exits.append, status.append, connect=connect, ping_interval=5,
                  backoff=Backoff(initial=1, maximum=8, jitter=0), clock=clock, sleep=delays.append)
    link.run()
    assert delays == [1, 2, 4, 1]  # 3 failures back off, a healthy session resets the backoff
    assert status == [True, False]
    assert [e["event_id"] for e in exits] == [1]
    assert link.connects == 1


class FakeResp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def test_image_cache_sends_device_key_and_caches():
    calls = []

    def opener(req, timeout):
        calls.append((req.full_url, req.get_header("X-device-key")))
        return FakeResp(b"JPEG")

    c = ImageCache("http://edge:8000/", "k1", opener=opener, capacity=2)
    assert c.fetch("/api/images/a.jpg") == b"JPEG"
    assert c.fetch("/api/images/a.jpg") == b"JPEG"
    assert calls == [("http://edge:8000/api/images/a.jpg", "k1")]
    c.fetch("/api/images/b.jpg")
    c.fetch("/api/images/c.jpg")
    assert c.get("/api/images/a.jpg") is None  # LRU evicted
    assert c.fetch("relative/path") is None


def test_image_cache_failure_returns_none():
    def opener(req, timeout):
        raise urllib.error.URLError("down")

    assert ImageCache("http://e", "k", opener=opener).fetch("/api/images/x.jpg") is None


def test_heartbeat_body_and_post():
    body = heartbeat_body("AU-G2", "G2", ws_connected=True, display_ok=True, uptime_s=12.7, cpu_temp=51.2)
    assert body == {"device_id": "AU-G2", "kind": "ALERT_UNIT", "gate_id": "G2",
                    "metrics": {"ws_connected": True, "display_ok": True, "uptime_s": 12, "cpu_temp_c": 51.2}}
    seen = {}

    def opener(req, timeout):
        seen.update(url=req.full_url, key=req.get_header("X-device-key"), method=req.get_method(),
                    body=json.loads(req.data))
        return FakeResp(b"{}")

    assert post_heartbeat("http://edge:8000", "k", body, opener=opener)
    assert seen["url"] == "http://edge:8000/api/devices/heartbeat" and seen["method"] == "POST"
    assert seen["key"] == "k" and seen["body"]["kind"] == "ALERT_UNIT"

    def down(req, timeout):
        raise OSError("no route")

    assert not post_heartbeat("http://edge:8000", "k", body, opener=down)


def test_heartbeat_beat_uses_metrics():
    got = {}

    def opener(req, timeout):
        got.update(json.loads(req.data))
        return FakeResp(b"{}")

    hb = Heartbeat("http://e", "k", "AU-G1", "G1", lambda: {"ws_connected": False, "display_ok": True, "exits_seen": 3},
                   opener=opener)
    assert hb.beat()
    assert got["device_id"] == "AU-G1" and got["metrics"]["ws_connected"] is False
    assert got["metrics"]["exits_seen"] == 3 and "uptime_s" in got["metrics"]


def test_configure_tls_passes_site_ca_to_websocket_and_https(monkeypatch, tmp_path):
    import ssl

    from exit_alert import net

    seen = {}

    class FakeWS:
        @staticmethod
        def create_connection(url, **kw):
            seen.update(kw)
            return object()

    monkeypatch.setitem(__import__("sys").modules, "websocket", FakeWS)
    monkeypatch.setattr(ssl, "create_default_context", lambda cafile=None: ("ctx", cafile))
    net.configure_tls(str(tmp_path / "site-ca.crt"))
    net.default_connect("wss://edge/ws/device", 5)
    assert seen["sslopt"] == {"ca_certs": str(tmp_path / "site-ca.crt")}
    assert net._ssl_context == ("ctx", str(tmp_path / "site-ca.crt"))
    net.configure_tls(None)
    seen.clear()
    net.default_connect("ws://edge/ws/device", 5)
    assert "sslopt" not in seen and net._ssl_context is None
