"""In-order event delivery from the outbox with exponential backoff."""

from __future__ import annotations

import logging
import random
import threading
import time
from typing import Callable

from .backend import EventSink, SendOutcome
from .metrics import Rolling, now_ms
from .outbox import Outbox

log = logging.getLogger(__name__)


class Emitter:
    """Delivers the outbox head, strictly in sequence order.

    While the head fails transiently the emitter backs off exponentially
    (``retry_initial_s`` doubling up to ``retry_max_s``, with jitter) and
    retries *the same event*; nothing behind it is sent first.
    """

    def __init__(
        self,
        outbox: Outbox,
        sink: EventSink,
        retry_initial_s: float = 1.0,
        retry_max_s: float = 30.0,
        clock_ms: Callable[[], int] = now_ms,
        sleep: Callable[[float], None] = time.sleep,
        jitter: bool = True,
    ) -> None:
        self.outbox = outbox
        self.sink = sink
        self.retry_initial_s = retry_initial_s
        self.retry_max_s = retry_max_s
        self.clock_ms = clock_ms
        self.sleep = sleep
        self.jitter = jitter
        self._backoff_s = 0.0
        self.delivered = 0
        self.delivery_latency_ms = Rolling(500)
        self.backend_ok = True

    def _next_backoff(self) -> float:
        self._backoff_s = self.retry_initial_s if self._backoff_s <= 0 else min(self.retry_max_s, self._backoff_s * 2)
        if self.jitter:
            return self._backoff_s * random.uniform(0.8, 1.2)
        return self._backoff_s

    def deliver_once(self) -> bool | None:
        """Try to deliver the head.

        Returns True if an event was delivered (or dead-lettered), False if the
        attempt failed / is backing off, None if the outbox is empty.
        """
        item = self.outbox.head()
        if item is None:
            return None
        now = self.clock_ms()
        if item.next_attempt_ms > now:
            return False
        outcome, info = self.sink.send(item.payload)
        if outcome == SendOutcome.OK:
            self.outbox.ack(item.seq)
            self.delivered += 1
            self._backoff_s = 0.0
            if not self.backend_ok:
                log.info("backend reachable again; delivering backlog (%d queued)", self.outbox.depth())
            self.backend_ok = True
            ts = item.payload.get("ts_ms")
            if isinstance(ts, int):
                self.delivery_latency_ms.add(max(0, self.clock_ms() - ts))
            return True
        if outcome == SendOutcome.REJECT:
            log.error("event %s rejected permanently (%s); moved to dead-letter", item.event_id, info)
            self.outbox.dead_letter(item.seq, info)
            return True
        delay = self._next_backoff()
        if self.backend_ok:
            log.warning("delivery of %s failed (%s); buffering locally, retry in %.1fs", item.event_id, info, delay)
        else:
            log.debug("delivery of %s failed again (%s); retry in %.1fs", item.event_id, info, delay)
        self.backend_ok = False
        self.outbox.fail(item.seq, info, now + int(delay * 1000))
        return False

    def drain(self, deadline_s: float | None = None, max_items: int | None = None) -> int:
        """Deliver until empty, blocked by backoff past the deadline, or ``max_items``."""
        start = time.monotonic()
        sent = 0
        while True:
            if max_items is not None and sent >= max_items:
                return sent
            res = self.deliver_once()
            if res is None:
                return sent
            if res:
                sent += 1
                continue
            if deadline_s is None or time.monotonic() - start >= deadline_s:
                return sent
            head = self.outbox.head()
            wait = 0.05 if head is None else max(0.0, (head.next_attempt_ms - self.clock_ms()) / 1000.0)
            remaining = deadline_s - (time.monotonic() - start)
            self.sleep(max(0.0, min(wait, remaining, 1.0)))

    def run(self, stop: threading.Event | None = None, idle_s: float = 0.05,
            should_stop: Callable[[], bool] | None = None) -> None:
        """Deliver forever (until ``stop`` is set or ``should_stop()`` returns True)."""
        while True:
            if stop is not None and stop.is_set():
                return
            res = self.deliver_once()
            if res is None:
                if should_stop is not None and should_stop():
                    return
                self.sleep(idle_s)
            elif res is False:
                head = self.outbox.head()
                wait = idle_s if head is None else max(idle_s, (head.next_attempt_ms - self.clock_ms()) / 1000.0)
                self.sleep(min(wait, 1.0))
