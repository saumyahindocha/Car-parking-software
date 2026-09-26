"""Timing instrumentation and rolling statistics."""

from __future__ import annotations

import time
from collections import deque
from contextlib import contextmanager
from typing import Iterator


def now_ms() -> int:
    return int(time.time() * 1000)


class Rolling:
    """Rolling window of samples with mean / percentile helpers."""

    def __init__(self, size: int = 500) -> None:
        self._values: deque[float] = deque(maxlen=size)

    def add(self, value: float) -> None:
        self._values.append(float(value))

    def __len__(self) -> int:
        return len(self._values)

    @property
    def mean(self) -> float:
        return sum(self._values) / len(self._values) if self._values else 0.0

    def percentile(self, q: float) -> float:
        if not self._values:
            return 0.0
        data = sorted(self._values)
        k = min(len(data) - 1, max(0, int(round(q / 100.0 * (len(data) - 1)))))
        return data[k]

    @property
    def max(self) -> float:
        return max(self._values) if self._values else 0.0


class StageTimer:
    """Per-stage wall-clock timings (ms) for the per-frame pipeline."""

    def __init__(self, window: int = 300) -> None:
        self.stages: dict[str, Rolling] = {}
        self._window = window

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.add(name, (time.perf_counter() - t0) * 1000.0)

    def add(self, name: str, ms: float) -> None:
        r = self.stages.get(name)
        if r is None:
            r = self.stages[name] = Rolling(self._window)
        r.add(ms)

    def summary(self) -> dict[str, dict[str, float]]:
        return {
            k: {"mean_ms": round(v.mean, 2), "p95_ms": round(v.percentile(95), 2), "max_ms": round(v.max, 2)}
            for k, v in self.stages.items()
        }


class RateMeter:
    """Events per second over a sliding time window."""

    def __init__(self, window_s: float = 10.0) -> None:
        self.window_s = window_s
        self._stamps: deque[float] = deque()

    def tick(self, t: float | None = None) -> None:
        t = time.monotonic() if t is None else t
        self._stamps.append(t)
        self._trim(t)

    def rate(self, t: float | None = None) -> float:
        t = time.monotonic() if t is None else t
        self._trim(t)
        if len(self._stamps) < 2:
            return 0.0
        span = max(self._stamps[-1] - self._stamps[0], 1e-6)
        return (len(self._stamps) - 1) / span

    def _trim(self, t: float) -> None:
        while self._stamps and t - self._stamps[0] > self.window_s:
            self._stamps.popleft()
