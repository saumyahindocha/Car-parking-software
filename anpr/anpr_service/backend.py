"""HTTP client for the backend: event delivery, heartbeats and config fetch.

All requests carry ``X-Device-Key: <ANPR_API_KEY>``.  An ``httpx`` transport
can be injected (tests use ``httpx.MockTransport``).
"""

from __future__ import annotations

import enum
import json
import logging
from pathlib import Path
from typing import Any, Protocol

import httpx

from .config import BackendConfig

log = logging.getLogger(__name__)


class SendOutcome(enum.Enum):
    OK = "ok"  # delivered (or already known to the backend)
    RETRY = "retry"  # transient: backend down, 5xx, timeout, 408/429
    REJECT = "reject"  # permanent: malformed / unauthorised -> dead-letter


class EventSink(Protocol):
    def send(self, payload: dict[str, Any]) -> tuple[SendOutcome, str]: ...

    def close(self) -> None: ...


def classify_status(code: int) -> SendOutcome:
    if 200 <= code < 300 or code == 409:  # 409: event_id already stored -> idempotent success
        return SendOutcome.OK
    if code in (408, 425, 429) or code >= 500:
        return SendOutcome.RETRY
    if code in (401, 403):
        # Wrong/rotated device key is an operator problem, not a bad event: keep retrying.
        return SendOutcome.RETRY
    return SendOutcome.REJECT


class BackendClient:
    def __init__(self, cfg: BackendConfig, transport: httpx.BaseTransport | None = None) -> None:
        self.cfg = cfg
        headers = {"X-Device-Key": cfg.api_key, "User-Agent": "anpr-service/0.1"}
        self._client = httpx.Client(
            base_url=cfg.url.rstrip("/") if cfg.url else "http://backend.invalid",
            headers=headers,
            timeout=httpx.Timeout(cfg.timeout_s, connect=min(2.0, cfg.timeout_s)),
            transport=transport,
        )

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.url)

    def close(self) -> None:
        self._client.close()

    # EventSink
    def send(self, payload: dict[str, Any]) -> tuple[SendOutcome, str]:
        try:
            r = self._client.post(self.cfg.events_path, json=payload)
        except httpx.HTTPError as exc:
            return SendOutcome.RETRY, f"{type(exc).__name__}: {exc}"
        outcome = classify_status(r.status_code)
        return outcome, f"HTTP {r.status_code}"

    def heartbeat(self, payload: dict[str, Any]) -> bool:
        try:
            r = self._client.post(self.cfg.heartbeat_path, json=payload)
            return 200 <= r.status_code < 300
        except httpx.HTTPError as exc:
            log.debug("heartbeat failed: %s", exc)
            return False

    def fetch_config(self) -> dict[str, Any] | None:
        try:
            r = self._client.get(self.cfg.config_path)
        except httpx.HTTPError as exc:
            log.warning("config fetch failed: %s", exc)
            return None
        if r.status_code != 200:
            log.warning("config fetch: HTTP %s", r.status_code)
            return None
        try:
            data = r.json()
        except ValueError:
            log.warning("config fetch: invalid JSON")
            return None
        return data if isinstance(data, dict) else None


class JsonlSink:
    """Appends events to a JSONL file (demo / offline mode, or as an audit copy)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def send(self, payload: dict[str, Any]) -> tuple[SendOutcome, str]:
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, separators=(",", ":")) + "\n")
            fh.flush()
        return SendOutcome.OK, "written"

    def close(self) -> None:
        return None


class TeeSink:
    """Deliver to a primary sink; on success also copy to secondary sinks."""

    def __init__(self, primary: EventSink, *copies: EventSink) -> None:
        self.primary = primary
        self.copies = copies

    def send(self, payload: dict[str, Any]) -> tuple[SendOutcome, str]:
        outcome, info = self.primary.send(payload)
        if outcome == SendOutcome.OK:
            for c in self.copies:
                try:
                    c.send(payload)
                except OSError:
                    log.exception("copy sink failed")
        return outcome, info

    def close(self) -> None:
        self.primary.close()
        for c in self.copies:
            c.close()
