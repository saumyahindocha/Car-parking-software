"""Per-camera heartbeat to ``POST {backend}/api/devices/heartbeat`` (every 10 s by default)."""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from .backend import BackendClient

log = logging.getLogger(__name__)


def heartbeat_payload(device_id: str, gate_id: str, metrics: dict[str, Any]) -> dict[str, Any]:
    return {"device_id": device_id, "kind": "CAMERA", "gate_id": gate_id, "metrics": metrics}


class HeartbeatThread(threading.Thread):
    """Background thread so a slow backend never blocks frame processing."""

    def __init__(
        self,
        client: BackendClient,
        device_id: str,
        gate_id: str,
        metrics_fn: Callable[[], dict[str, Any]],
        interval_s: float = 10.0,
    ) -> None:
        super().__init__(name=f"heartbeat-{device_id}", daemon=True)
        self.client = client
        self.device_id = device_id
        self.gate_id = gate_id
        self.metrics_fn = metrics_fn
        self.interval_s = interval_s
        self._stop_evt = threading.Event()
        self.sent = 0

    def run(self) -> None:
        while not self._stop_evt.wait(self.interval_s):
            self.beat()

    def beat(self) -> bool:
        try:
            metrics = self.metrics_fn()
        except Exception:  # noqa: BLE001 - metrics must never kill the thread
            log.exception("heartbeat metrics failed for %s", self.device_id)
            return False
        ok = self.client.heartbeat(heartbeat_payload(self.device_id, self.gate_id, metrics))
        if ok:
            self.sent += 1
        return ok

    def stop(self) -> None:
        self._stop_evt.set()
