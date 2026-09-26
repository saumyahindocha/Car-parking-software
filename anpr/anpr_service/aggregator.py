"""Gate aggregator: cross-camera merge, UNREAD merging, direction and de-duplication.

Both ANPR cameras of a gate overlap, so one vehicle can produce a
``CameraRead`` from each.  Reads are clustered per gate:

* READ + READ: same plate under confusion-aware equality within
  ``merge_window_s`` -> one event (highest-confidence read wins, every
  camera's images are kept, ``camera_ids`` lists all cameras).
* UNREAD + anything from a *different* camera: crossing times within
  ``unread_merge_s`` and lateral positions (fraction of gate width) within
  ``unread_merge_dx`` -> same vehicle, so an UNREAD from one camera is
  absorbed by the READ of the other.
* A cluster is emitted once every live ANPR camera of the gate has processed
  frames up to ``first crossing + merge_hold_s`` (event-time watermarks), or
  as soon as all ANPR cameras have contributed.  Watermarks make merging
  correct in real time *and* in as-fast-as-possible replay, and keep latency
  at roughly ``merge_hold_s`` (0.8 s default) after the crossing.
* After emission: a later read of the same plate within ``merge_window_s``
  is folded into the emitted event (suppressed); within ``dedupe_window_s``
  it is dropped as a duplicate.

This module is pure logic (no I/O) so it can be unit-tested with synthetic
reads and an injected clock.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from .config import GateConfig, Settings
from .plates import DEFAULT_RULES, PlateRules, approx_match, canonical, plates_equal
from .types import (
    CameraRead,
    Direction,
    GateDirection,
    OverviewSnapshot,
    PlateCandidate,
    ReadStatus,
    VehicleClass,
)

log = logging.getLogger(__name__)

INF_TS = 2**62


def resolve_direction(gate_direction: GateDirection, travel_sign: int) -> tuple[Direction, bool]:
    """Map observed travel to (direction, wrong_way) for the gate's current mode."""
    observed = Direction.IN if travel_sign > 0 else Direction.OUT
    if gate_direction == GateDirection.BOTH:
        return observed, False
    return observed, observed.value != gate_direction.value


@dataclass
class _Cluster:
    reads: list[CameraRead]
    first_ts: int
    created_mono: float

    @property
    def cameras(self) -> set[str]:
        return {r.camera_id for r in self.reads}

    @property
    def best_read(self) -> CameraRead | None:
        read = [r for r in self.reads if r.status == ReadStatus.READ]
        return max(read, key=lambda r: r.confidence) if read else None

    @property
    def lateral(self) -> float:
        best = self.best_read
        if best is not None:
            return best.lateral
        return sum(r.lateral for r in self.reads) / len(self.reads)


@dataclass
class MergedEvent:
    event_id: str
    gate_id: str
    camera_ids: list[str]
    direction: Direction
    wrong_way: bool
    vehicle_class: VehicleClass
    ts_ms: int
    status: ReadStatus
    plate: str | None
    confidence: float
    candidates: list[PlateCandidate]
    primary: CameraRead  # read whose images become plate_crop / full_frame
    others: list[CameraRead] = field(default_factory=list)
    overview_jpeg: bytes | None = None
    first_grab_wall_ms: int = 0
    lateral: float = 0.5


@dataclass
class _Emitted:
    event_id: str
    plate: str | None
    canon: str | None
    ts_ms: int
    lateral: float
    cameras: set[str]


@dataclass
class AggregatorStats:
    reads: int = 0
    events: int = 0
    merged_cross_camera: int = 0
    merged_unread: int = 0
    suppressed_late: int = 0
    deduped: int = 0


class GateAggregator:
    def __init__(
        self,
        gate: GateConfig,
        settings: Settings,
        rules: PlateRules = DEFAULT_RULES,
        anpr_camera_ids: list[str] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.gate = gate
        self.settings = settings
        self.rules = rules
        self.clock = clock
        self.anpr_cameras: list[str] = list(anpr_camera_ids or [c.id for c in gate.anpr_cameras()])
        self._pending: list[_Cluster] = []
        self._emitted: deque[_Emitted] = deque()
        self._watermarks: dict[str, int] = {}
        self._last_seen: dict[str, float] = {}
        self._eos: set[str] = set()
        self._overview: deque[OverviewSnapshot] = deque()
        self.stats = AggregatorStats()

    # --------------------------------------------------------------- config
    def update_config(self, gate: GateConfig, settings: Settings) -> None:
        if gate.direction != self.gate.direction:
            log.info("gate %s direction %s -> %s", gate.id, self.gate.direction.value, gate.direction.value)
        self.gate = gate
        self.settings = settings

    # ---------------------------------------------------------------- input
    def watermark(self, camera_id: str, ts_ms: int) -> None:
        if ts_ms > self._watermarks.get(camera_id, -1):
            self._watermarks[camera_id] = ts_ms
        self._last_seen[camera_id] = self.clock()

    def end_of_stream(self, camera_id: str) -> None:
        self._eos.add(camera_id)
        self._watermarks[camera_id] = INF_TS

    def add_overview(self, snap: OverviewSnapshot) -> None:
        self._overview.append(snap)
        horizon = snap.ts_ms - int(self.settings.overview_buffer_s * 1000)
        while self._overview and self._overview[0].ts_ms < horizon:
            self._overview.popleft()

    def add_read(self, read: CameraRead) -> None:
        self.stats.reads += 1
        self.watermark(read.camera_id, read.ts_ms)
        s = self.settings
        merge_ms = int(s.merge_window_s * 1000)
        dedupe_ms = int(s.dedupe_window_s * 1000)
        unread_ms = int(s.unread_merge_s * 1000)

        if read.status == ReadStatus.READ and read.plate:
            # 1) Already emitted?  Late merge or duplicate.
            for e in reversed(self._emitted):
                if e.canon is None or not plates_equal(e.plate, read.plate):
                    continue
                dt = abs(read.ts_ms - e.ts_ms)
                if dt <= merge_ms:
                    self.stats.suppressed_late += 1
                    log.info("gate=%s late read %s from %s merged into emitted event %s",
                             self.gate.id, read.plate, read.camera_id, e.event_id)
                    return
                if dt <= dedupe_ms:
                    self.stats.deduped += 1
                    log.info("gate=%s duplicate %s within %.0fs dropped", self.gate.id, read.plate, s.dedupe_window_s)
                    return
            # 2) Pending cluster with the same plate.
            for c in self._pending:
                b = c.best_read
                if b is not None and plates_equal(b.plate, read.plate) and abs(read.ts_ms - c.first_ts) <= merge_ms:
                    self._join(c, read, cross_camera=read.camera_id not in c.cameras)
                    return
            # 2b) Same crossing seen by the other camera with a near-identical
            #     read (one edit apart): physically the same vehicle.
            for c in self._pending:
                b = c.best_read
                if (
                    b is not None
                    and read.camera_id not in c.cameras
                    and abs(read.ts_ms - c.first_ts) <= unread_ms
                    and abs(read.lateral - c.lateral) <= s.unread_merge_dx
                    and approx_match(b.plate, read.plate, 1)
                ):
                    self._join(c, read, cross_camera=True)
                    return
            # 3) Pending UNREAD-only cluster of the same crossing from another camera.
            c = self._closest_unread_partner(read, only_unread_clusters=True, window_ms=unread_ms)
            if c is not None:
                self.stats.merged_unread += 1
                self._join(c, read, cross_camera=True)
                return
            self._new_cluster(read)
            return

        # UNREAD
        c = self._closest_unread_partner(read, only_unread_clusters=False, window_ms=unread_ms)
        if c is not None:
            self.stats.merged_unread += 1
            self._join(c, read, cross_camera=True)
            return
        for e in reversed(self._emitted):
            if (
                read.camera_id not in e.cameras
                and abs(read.ts_ms - e.ts_ms) <= unread_ms
                and abs(read.lateral - e.lateral) <= s.unread_merge_dx
            ):
                self.stats.suppressed_late += 1
                log.info("gate=%s late UNREAD from %s folded into emitted event %s",
                         self.gate.id, read.camera_id, e.event_id)
                return
        self._new_cluster(read)

    # --------------------------------------------------------------- output
    def poll(self, force: bool = False) -> list[MergedEvent]:
        """Emit every cluster whose merge hold has elapsed (or all, if ``force``)."""
        out: list[MergedEvent] = []
        if not self._pending:
            self._prune()
            return out
        hold_ms = int(self.settings.merge_hold_s * 1000)
        ready: list[_Cluster] = []
        keep: list[_Cluster] = []
        low_wm = self._low_watermark()
        all_cams = set(self.anpr_cameras)
        for c in self._pending:
            complete = bool(all_cams) and all_cams.issubset(c.cameras)
            if force or complete or low_wm >= c.first_ts + hold_ms:
                ready.append(c)
            else:
                keep.append(c)
        self._pending = keep
        for c in sorted(ready, key=lambda c: c.first_ts):
            ev = self._emit(c)
            if ev is not None:
                out.append(ev)
        self._prune()
        return out

    @property
    def pending(self) -> int:
        return len(self._pending)

    # ------------------------------------------------------------- internals
    def _low_watermark(self) -> int:
        """Lowest event-time watermark among live ANPR cameras.

        Cameras that are at EOS or silent for ``camera_stale_s`` do not hold
        back emission (a dead camera must not stall the gate)."""
        now = self.clock()
        stale = self.settings.camera_stale_s
        marks = []
        for cam in self.anpr_cameras:
            if cam in self._eos:
                continue
            seen = self._last_seen.get(cam)
            if seen is None or now - seen > stale:
                continue
            marks.append(self._watermarks.get(cam, -1))
        if not marks:
            return INF_TS
        return min(marks)

    def _closest_unread_partner(
        self, read: CameraRead, only_unread_clusters: bool, window_ms: int
    ) -> _Cluster | None:
        best: tuple[float, _Cluster] | None = None
        for c in self._pending:
            if read.camera_id in c.cameras:
                continue  # one camera never sees the same vehicle twice
            if only_unread_clusters and c.best_read is not None:
                continue
            dt = abs(read.ts_ms - c.first_ts)
            dx = abs(read.lateral - c.lateral)
            if dt > window_ms or dx > self.settings.unread_merge_dx:
                continue
            cost = dt / max(window_ms, 1) + dx / max(self.settings.unread_merge_dx, 1e-6)
            if best is None or cost < best[0]:
                best = (cost, c)
        return best[1] if best else None

    def _new_cluster(self, read: CameraRead) -> None:
        self._pending.append(_Cluster(reads=[read], first_ts=read.ts_ms, created_mono=self.clock()))

    def _join(self, c: _Cluster, read: CameraRead, cross_camera: bool) -> None:
        c.reads.append(read)
        c.first_ts = min(c.first_ts, read.ts_ms)
        if cross_camera:
            self.stats.merged_cross_camera += 1

    def _emit(self, c: _Cluster) -> MergedEvent | None:
        best = c.best_read
        primary = best or max(c.reads, key=lambda r: (r.plate_jpeg is not None, r.n_votes, r.confidence))
        if best is not None:
            # Final dedupe check against events emitted meanwhile.
            dedupe_ms = int(self.settings.dedupe_window_s * 1000)
            for e in reversed(self._emitted):
                if e.canon is not None and plates_equal(e.plate, best.plate) and abs(e.ts_ms - c.first_ts) <= dedupe_ms:
                    self.stats.deduped += 1
                    log.info("gate=%s duplicate %s dropped at emission", self.gate.id, best.plate)
                    return None
        direction, wrong_way = resolve_direction(self.gate.direction, primary.travel_sign)
        candidates = self._merge_candidates(c.reads)
        classes = [r.vehicle_class for r in c.reads]
        vclass = primary.vehicle_class if primary.vehicle_class != VehicleClass.OTHER else max(
            set(classes), key=classes.count
        )
        event = MergedEvent(
            event_id=str(uuid.uuid4()),
            gate_id=self.gate.id,
            camera_ids=sorted({r.camera_id for r in c.reads}),
            direction=direction,
            wrong_way=wrong_way,
            vehicle_class=vclass,
            ts_ms=c.first_ts,
            status=ReadStatus.READ if best is not None else ReadStatus.UNREAD,
            plate=best.plate if best is not None else None,
            confidence=round(best.confidence if best is not None else max(r.confidence for r in c.reads), 4),
            candidates=candidates,
            primary=primary,
            others=[r for r in c.reads if r is not primary],
            overview_jpeg=self._overview_for(c.first_ts),
            first_grab_wall_ms=min(r.grab_wall_ms for r in c.reads),
            lateral=c.lateral,
        )
        self._emitted.append(
            _Emitted(
                event_id=event.event_id,
                plate=event.plate,
                canon=canonical(event.plate) if event.plate else None,
                ts_ms=event.ts_ms,
                lateral=event.lateral,
                cameras=c.cameras,
            )
        )
        self.stats.events += 1
        log.info(
            "gate=%s EVENT %s %s plate=%s conf=%.2f dir=%s wrong_way=%s cameras=%s",
            self.gate.id, event.event_id, event.status.value, event.plate, event.confidence,
            event.direction.value, event.wrong_way, ",".join(event.camera_ids),
        )
        return event

    def _merge_candidates(self, reads: list[CameraRead]) -> list[PlateCandidate]:
        best_by_canon: dict[str, PlateCandidate] = {}
        for r in reads:
            for cand in r.candidates:
                key = canonical(cand.plate)
                cur = best_by_canon.get(key)
                if cur is None or cand.confidence > cur.confidence:
                    best_by_canon[key] = PlateCandidate(cand.plate, cand.confidence)
        return sorted(best_by_canon.values(), key=lambda c: c.confidence, reverse=True)[:3]

    def _overview_for(self, ts_ms: int) -> bytes | None:
        if not self._overview:
            return None
        snap = min(self._overview, key=lambda s: abs(s.ts_ms - ts_ms))
        if abs(snap.ts_ms - ts_ms) > 3000:
            return None
        return snap.jpeg

    def _prune(self) -> None:
        horizon_ms = int(max(self.settings.dedupe_window_s, self.settings.merge_window_s) * 1000) + 5000
        finite = [w for w in self._watermarks.values() if w < INF_TS]
        latest = max(finite) if finite else None
        if latest is None:
            return
        while self._emitted and latest - self._emitted[0].ts_ms > horizon_ms:
            self._emitted.popleft()
