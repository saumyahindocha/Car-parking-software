"""Thin network layer: WebSocket subscription with reconnect/backoff, image fetch, heartbeat.

Everything takes its transport as a parameter (connect function, HTTP opener, clock, sleep) so the
logic is tested with fakes; production uses `websocket-client` and `urllib`.
"""
from __future__ import annotations

import json
import logging
import random
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from typing import Any, Callable, Optional, Protocol

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------- backoff
class Backoff:
    """Exponential backoff with +-jitter, capped; `reset()` after a healthy connection."""

    def __init__(self, initial: float = 1.0, maximum: float = 30.0, factor: float = 2.0, jitter: float = 0.2,
                 rng: Optional[random.Random] = None):
        self.initial, self.maximum, self.factor, self.jitter = initial, maximum, factor, jitter
        self.rng = rng or random.Random()
        self.attempt = 0

    def next(self) -> float:
        base = min(self.maximum, self.initial * (self.factor ** self.attempt))
        self.attempt += 1
        if self.jitter:
            base *= 1 + self.rng.uniform(-self.jitter, self.jitter)
        return max(0.0, min(base, self.maximum))

    def reset(self) -> None:
        self.attempt = 0


# ---------------------------------------------------------------------------- websocket
class WsConnection(Protocol):
    def recv(self) -> str: ...

    def send(self, data: str) -> Any: ...

    def settimeout(self, timeout: float) -> None: ...

    def close(self) -> None: ...


def _timeout_types() -> tuple[type[BaseException], ...]:
    types: list[type[BaseException]] = [TimeoutError, socket.timeout]
    try:
        import websocket

        types.append(websocket.WebSocketTimeoutException)
    except ImportError:  # pragma: no cover
        pass
    return tuple(types)


TIMEOUTS = _timeout_types()


# TLS: the site gateway uses its own certificate authority (see deploy/ in the main repo).
# configure_tls(ca_file) makes every HTTPS/WSS call from this unit trust that CA.
_ca_file: Optional[str] = None
_ssl_context: Optional[ssl.SSLContext] = None


def configure_tls(ca_file: Optional[str]) -> None:
    global _ca_file, _ssl_context
    _ca_file = ca_file or None
    _ssl_context = ssl.create_default_context(cafile=_ca_file) if _ca_file else None


def default_connect(url: str, timeout: float) -> WsConnection:
    import websocket  # websocket-client

    kw: dict = {}
    if url.startswith("wss://") and _ca_file:
        kw["sslopt"] = {"ca_certs": _ca_file}
    return websocket.create_connection(url, timeout=timeout, enable_multithread=True, **kw)


class WsLink:
    """Keeps one subscription to /ws/device alive.

    * sends "ping" every `ping_interval` s; the server answers {"topic":"pong"}
    * if nothing at all arrives for 3 ping intervals the link is considered dead and re-opened
    * reconnects forever with exponential backoff; `on_status(bool)` reports connectivity
    """

    def __init__(self, url: str, on_exit: Callable[[dict], None], on_status: Callable[[bool], None], *,
                 connect: Callable[[str, float], WsConnection] = default_connect, ping_interval: float = 10.0,
                 backoff: Optional[Backoff] = None, clock: Callable[[], float] = time.monotonic,
                 sleep: Optional[Callable[[float], None]] = None):
        self.url = url
        self.on_exit, self.on_status = on_exit, on_status
        self.connect = connect
        self.ping_interval = ping_interval
        self.backoff = backoff or Backoff()
        self.clock = clock
        self.stop_event = threading.Event()
        self.sleep = sleep or (lambda s: self.stop_event.wait(s))
        self.connected = False
        self.messages = 0
        self.connects = 0

    def _set_status(self, ok: bool) -> None:
        if ok != self.connected:
            self.connected = ok
            self.on_status(ok)

    def handle_text(self, text: str) -> None:
        if text == "ping":
            return
        try:
            msg = json.loads(text)
        except ValueError:
            log.warning("ignoring non-JSON websocket frame")
            return
        if not isinstance(msg, dict):
            return
        if msg.get("topic") == "exit" and isinstance(msg.get("data"), dict):
            self.messages += 1
            self.on_exit(msg["data"])

    def run_once(self) -> None:
        """Connect and pump messages until the connection fails or `stop()` is called."""
        ws = self.connect(self.url, self.ping_interval)
        self.connects += 1
        try:
            ws.settimeout(1.0)
            self._set_status(True)
            last_rx = last_ping = self.clock()
            first_ok = False
            while not self.stop_event.is_set():
                now = self.clock()
                if now - last_ping >= self.ping_interval:
                    ws.send("ping")
                    last_ping = now
                if now - last_rx > 3 * self.ping_interval:
                    raise TimeoutError("websocket silent for too long")
                try:
                    text = ws.recv()
                except TIMEOUTS:
                    continue
                if text is None or text == "":
                    raise ConnectionError("websocket closed by server")
                last_rx = self.clock()
                if not first_ok:
                    first_ok = True
                    self.backoff.reset()  # the server talked to us: the link is healthy
                self.handle_text(text if isinstance(text, str) else text.decode("utf-8", "replace"))
        finally:
            try:
                ws.close()
            except Exception:
                pass

    def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.run_once()
            except Exception as exc:  # any failure -> offline + backoff
                log.warning("websocket: %s: %s", type(exc).__name__, exc)
            self._set_status(False)
            if self.stop_event.is_set():
                break
            delay = self.backoff.next()
            log.info("websocket: reconnecting in %.1fs", delay)
            self.sleep(delay)

    def stop(self) -> None:
        self.stop_event.set()


# ---------------------------------------------------------------------------- http
Opener = Callable[[urllib.request.Request, float], Any]


def _default_open(req: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.urlopen(req, timeout=timeout, context=_ssl_context)  # noqa: S310 - URL comes from config


class ImageCache:
    """Fetches /api/images/... with the device key; small LRU cache of raw bytes."""

    def __init__(self, edge_url: str, device_key: str, *, opener: Opener = _default_open, capacity: int = 16,
                 timeout: float = 3.0):
        self.edge_url = edge_url.rstrip("/")
        self.device_key = device_key
        self.opener = opener
        self.capacity = capacity
        self.timeout = timeout
        self._data: OrderedDict[str, bytes] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, path: str) -> Optional[bytes]:
        with self._lock:
            if path in self._data:
                self._data.move_to_end(path)
                return self._data[path]
        return None

    def put(self, path: str, data: bytes) -> None:
        with self._lock:
            self._data[path] = data
            self._data.move_to_end(path)
            while len(self._data) > self.capacity:
                self._data.popitem(last=False)

    def fetch(self, path: str) -> Optional[bytes]:
        cached = self.get(path)
        if cached is not None:
            return cached
        if not path.startswith("/"):
            return None
        req = urllib.request.Request(self.edge_url + path, headers={"X-Device-Key": self.device_key})
        try:
            with self.opener(req, self.timeout) as resp:
                data = resp.read()
        except (urllib.error.URLError, OSError, ValueError) as exc:
            log.info("image fetch failed: %s", exc)
            return None
        self.put(path, data)
        return data

    def fetch_async(self, path: str, done: Optional[Callable[[str, Optional[bytes]], None]] = None) -> None:
        def work() -> None:
            data = self.fetch(path)
            if done:
                done(path, data)

        threading.Thread(target=work, name="image-fetch", daemon=True).start()


def cpu_temp_c(path: str = "/sys/class/thermal/thermal_zone0/temp") -> Optional[float]:
    try:
        with open(path, encoding="ascii") as fh:
            return round(int(fh.read().strip()) / 1000.0, 1)
    except (OSError, ValueError):
        return None


def heartbeat_body(device_id: str, gate_id: str, *, ws_connected: bool, display_ok: bool, uptime_s: float,
                   cpu_temp: Optional[float], extra: Optional[dict] = None) -> dict:
    metrics: dict[str, Any] = {"ws_connected": ws_connected, "display_ok": display_ok, "uptime_s": int(uptime_s),
                               "cpu_temp_c": cpu_temp}
    metrics.update(extra or {})
    return {"device_id": device_id, "kind": "ALERT_UNIT", "gate_id": gate_id, "metrics": metrics}


def post_heartbeat(edge_url: str, device_key: str, body: dict, *, opener: Opener = _default_open,
                   timeout: float = 5.0) -> bool:
    req = urllib.request.Request(edge_url.rstrip("/") + "/api/devices/heartbeat", data=json.dumps(body).encode(),
                                 headers={"X-Device-Key": device_key, "Content-Type": "application/json"},
                                 method="POST")
    try:
        with opener(req, timeout) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log.info("heartbeat failed: %s", exc)
        return False


class Heartbeat:
    """Background thread posting the heartbeat every `interval` seconds."""

    def __init__(self, edge_url: str, device_key: str, device_id: str, gate_id: str,
                 metrics: Callable[[], dict], interval: float = 15.0, opener: Opener = _default_open):
        self.edge_url, self.device_key, self.device_id, self.gate_id = edge_url, device_key, device_id, gate_id
        self.metrics = metrics
        self.interval = interval
        self.opener = opener
        self.stop_event = threading.Event()
        self.started = time.monotonic()
        self.last_ok: Optional[bool] = None

    def beat(self) -> bool:
        m = self.metrics()
        body = heartbeat_body(self.device_id, self.gate_id, ws_connected=m.pop("ws_connected", False),
                              display_ok=m.pop("display_ok", True), uptime_s=time.monotonic() - self.started,
                              cpu_temp=cpu_temp_c(), extra=m)
        self.last_ok = post_heartbeat(self.edge_url, self.device_key, body, opener=self.opener)
        return self.last_ok

    def run(self) -> None:
        while not self.stop_event.is_set():
            self.beat()
            self.stop_event.wait(self.interval)

    def stop(self) -> None:
        self.stop_event.set()
