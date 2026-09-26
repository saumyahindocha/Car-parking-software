"""Deterministic single-process replay runner.

Runs every camera pipeline, the gate aggregators, the image writer, the
outbox and (optionally) delivery in one process, interleaving the cameras'
frames in timestamp order.  Used by the end-to-end tests, the evaluation
command and ``replay --inline``; production uses the multi-process
supervisor in :mod:`anpr_service.service`, which runs the same components.
"""

from __future__ import annotations

import heapq
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .aggregator import GateAggregator
from .backend import EventSink
from .config import CameraConfig, GateConfig, ServiceConfig
from .emitter import Emitter
from .events import EventBuilder
from .imaging import ImageStore, encode_jpeg
from .ingest import FramePacket, ReplaySource
from .metrics import Rolling, now_ms
from .outbox import Outbox
from .pipeline import CameraPipeline
from .recognizers import PlateRecognizer, create_recognizer
from .types import CameraRead, CameraRole, OverviewSnapshot

log = logging.getLogger(__name__)


@dataclass
class ReplayResult:
    events: list[dict[str, Any]] = field(default_factory=list)
    reads: list[CameraRead] = field(default_factory=list)
    frames: dict[str, int] = field(default_factory=dict)
    wall_s: float = 0.0
    timings: dict[str, dict[str, dict[str, float]]] = field(default_factory=dict)
    aggregation: dict[str, dict[str, int]] = field(default_factory=dict)
    emit_latency_ms: dict[str, float] = field(default_factory=dict)
    delivered: int = 0
    outbox_depth: int = 0


def resolve_videos(cfg: ServiceConfig, videos: dict[str, str] | None) -> dict[str, str]:
    """camera_id -> file: explicit ``videos`` win, otherwise each camera's ``replay_file``."""
    if videos:
        for cid in videos:
            cfg.camera(cid)  # raises KeyError for unknown cameras
        return dict(videos)
    return {c.id: c.replay_file for _g, c in cfg.all_cameras() if c.enabled and c.replay_file}


class InlineReplayRunner:
    def __init__(
        self,
        cfg: ServiceConfig,
        videos: dict[str, str] | None = None,
        sink: EventSink | None = None,
        start_epoch_ms: int | None = None,
        recognizer_factory: Callable[[ServiceConfig], PlateRecognizer] = create_recognizer,
        keep_reads: bool = True,
        drain_timeout_s: float = 10.0,
    ) -> None:
        self.cfg = cfg
        self.videos = resolve_videos(cfg, videos)
        if not self.videos:
            raise ValueError("no replay videos: pass --video CAM=path or set replay_file in the config")
        self.sink = sink
        self.start_epoch_ms = start_epoch_ms if start_epoch_ms is not None else now_ms()
        self.recognizer_factory = recognizer_factory
        self.keep_reads = keep_reads
        self.drain_timeout_s = drain_timeout_s

    def run(self) -> ReplayResult:
        cfg = self.cfg
        t_start = time.perf_counter()
        result = ReplayResult()
        sources: dict[str, ReplaySource] = {}
        pipelines: dict[str, CameraPipeline] = {}
        cams: dict[str, tuple[GateConfig, CameraConfig]] = {}
        for cid, path in self.videos.items():
            gate, cam = cfg.camera(cid)
            cams[cid] = (gate, cam)
            sources[cid] = ReplaySource(path, start_epoch_ms=self.start_epoch_ms)
            if cam.role == CameraRole.ANPR:
                pipelines[cid] = CameraPipeline(cfg, gate, cam, self.recognizer_factory(cfg))
        rules = cfg.plates.to_rules()
        aggregators: dict[str, GateAggregator] = {}
        for gate_id in {g.id for g, _c in cams.values()}:
            gate = cfg.gate(gate_id)
            anpr_ids = [c.id for c in gate.anpr_cameras() if c.id in pipelines]
            aggregators[gate_id] = GateAggregator(gate, cfg.settings, rules, anpr_ids)
        outbox = Outbox(cfg.outbox_path)
        builder = EventBuilder(ImageStore(cfg.image_root))
        emitter = (
            Emitter(outbox, self.sink, cfg.emitter.retry_initial_s, cfg.emitter.retry_max_s)
            if self.sink is not None
            else None
        )
        emit_latency = Rolling(10000)
        last_snapshot: dict[str, int] = {}

        heap: list[tuple[int, int, str, FramePacket]] = []
        seq = 0

        def push(cid: str) -> None:
            nonlocal seq
            pkt = sources[cid].read()
            if pkt is not None:
                heapq.heappush(heap, (pkt.ts_ms, seq, cid, pkt))
                seq += 1
            else:
                gate, cam = cams[cid]
                agg = aggregators[gate.id]
                if cid in pipelines:
                    for r in pipelines[cid].flush():
                        self._add_read(agg, r, result)
                agg.end_of_stream(cid)

        for cid in sources:
            push(cid)

        def publish(events: list[Any]) -> None:
            for ev in events:
                payload = builder.build(ev)
                emit_latency.add(payload["latency_ms"])
                outbox.put(payload)
                result.events.append(payload)
            if emitter is not None and events:
                emitter.drain(deadline_s=0)

        while heap:
            _ts, _s, cid, pkt = heapq.heappop(heap)
            gate, cam = cams[cid]
            agg = aggregators[gate.id]
            result.frames[cid] = result.frames.get(cid, 0) + 1
            if cid in pipelines:
                for r in pipelines[cid].process(pkt.frame, pkt.ts_ms, pkt.grab_wall_ms):
                    self._add_read(agg, r, result)
                agg.watermark(cid, pkt.ts_ms)
            else:
                every = int(cfg.settings.overview_snapshot_s * 1000)
                if pkt.ts_ms - last_snapshot.get(cid, -10**12) >= every:
                    last_snapshot[cid] = pkt.ts_ms
                    agg.add_overview(OverviewSnapshot(cid, gate.id, pkt.ts_ms, encode_jpeg(pkt.frame, 80, 1280)))
            publish(agg.poll())
            push(cid)

        for agg in aggregators.values():
            publish(agg.poll(force=True))
        if emitter is not None:
            emitter.drain(deadline_s=self.drain_timeout_s)
            result.delivered = emitter.delivered
        result.outbox_depth = outbox.depth()
        outbox.close()
        for src in sources.values():
            src.close()
        for p in pipelines.values():
            p.recognizer.close()
            result.timings[p.camera.id] = p.timer.summary()
        result.aggregation = {gid: vars(a.stats).copy() for gid, a in aggregators.items()}
        result.emit_latency_ms = {
            "mean": round(emit_latency.mean, 1),
            "p95": round(emit_latency.percentile(95), 1),
            "max": round(emit_latency.max, 1),
        }
        result.wall_s = time.perf_counter() - t_start
        return result

    def _add_read(self, agg: GateAggregator, read: CameraRead, result: ReplayResult) -> None:
        agg.add_read(read)
        if self.keep_reads:
            result.reads.append(read)


__all__ = ["InlineReplayRunner", "ReplayResult", "resolve_videos"]
