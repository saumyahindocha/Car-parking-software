"""Multi-process ANPR service.

Process layout (``spawn`` start method)::

    supervisor (main)          config refresh, watchdog, signals
    ├── camera worker x N       one per camera stream: ingest -> pipeline -> CameraRead
    │                           (overview cameras send periodic snapshots instead)
    ├── gate aggregator x G     merge / UNREAD merge / dedupe -> images -> outbox
    └── emitter x 1             outbox -> backend, strictly in order, with backoff

Workers talk to their gate aggregator over a ``multiprocessing.Queue``
carrying small dataclasses (reads carry JPEG bytes, never raw frames).  The
outbox is a SQLite file shared by the aggregators (writers) and the emitter
(reader/deleter).
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import queue
import signal
import time
from dataclasses import dataclass
from multiprocessing.process import BaseProcess
from multiprocessing.synchronize import Event as MpEvent
from typing import Any

from .aggregator import GateAggregator
from .backend import BackendClient, EventSink, JsonlSink, TeeSink
from .config import ServiceConfig, apply_remote_config, redact_url
from .emitter import Emitter
from .events import EventBuilder
from .heartbeat import HeartbeatThread
from .imaging import ImageStore, encode_jpeg
from .ingest import FrameSource, ReplaySource, RtspSource
from .logutil import setup_logging
from .metrics import now_ms
from .outbox import Outbox
from .pipeline import CameraPipeline
from .recognizers import RecognizerUnavailable, create_recognizer
from .types import (
    CameraRead,
    CameraRole,
    ConfigUpdate,
    EndOfStream,
    OverviewSnapshot,
    Watermark,
)

log = logging.getLogger(__name__)

WATERMARK_EVERY_MS = 100
STATS_LOG_EVERY_S = 60.0


@dataclass
class ReplaySpec:
    path: str
    start_epoch_ms: int
    loop: bool = False
    realtime: bool = False


# --------------------------------------------------------------------------- workers
def camera_worker_main(
    cfg_data: dict[str, Any],
    camera_id: str,
    out_q: "mp.Queue[Any]",
    control_q: "mp.Queue[Any]",
    stop: MpEvent,
    replay: ReplaySpec | None,
    log_level: str,
) -> None:
    setup_logging(log_level)
    cfg = ServiceConfig.model_validate(cfg_data)
    gate, cam = cfg.camera(camera_id)
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # the supervisor coordinates shutdown
    source: FrameSource
    try:
        if replay is not None:
            source = ReplaySource(replay.path, replay.start_epoch_ms, loop=replay.loop, realtime=replay.realtime)
        else:
            source = RtspSource(cam.rtsp_url, cfg.ingest, cam.gstreamer_pipeline, name=camera_id)
    except (OSError, ValueError) as exc:
        log.error("camera %s: cannot open source: %s", camera_id, exc)
        out_q.put(EndOfStream(camera_id))
        return
    pipeline: CameraPipeline | None = None
    if cam.role == CameraRole.ANPR:
        try:
            pipeline = CameraPipeline(cfg, gate, cam, create_recognizer(cfg))
        except RecognizerUnavailable as exc:
            log.error("camera %s: recogniser unavailable: %s", camera_id, exc)
            out_q.put(EndOfStream(camera_id))
            source.close()
            return

    backend = BackendClient(cfg.backend) if cfg.backend.url else None
    hb: HeartbeatThread | None = None
    processed = {"n": 0, "t0": time.monotonic()}

    def metrics() -> dict[str, Any]:
        elapsed = max(time.monotonic() - processed["t0"], 1e-6)
        fps = processed["n"] / elapsed
        processed["n"], processed["t0"] = 0, time.monotonic()
        return {
            "fps": round(fps, 2),
            "last_frame_ts_ms": source.last_frame_ts_ms,
            "read_rate": round(pipeline.read_rate, 3) if pipeline else 1.0,
            "stream_ok": bool(source.stream_ok),
            "queue_depth": int(source.queue_depth),
        }

    if backend is not None:
        hb = HeartbeatThread(backend, camera_id, gate.id, metrics, cfg.settings.heartbeat_s)
        hb.start()

    last_wm = -(10**12)
    last_snap = -(10**12)
    last_stats = time.monotonic()
    last_error = 0.0
    log.info("camera %s (%s, gate %s) started: %s", camera_id, cam.role.value, gate.id,
             replay.path if replay else redact_url(cam.rtsp_url or cam.gstreamer_pipeline))
    try:
        while not stop.is_set():
            _drain_control(control_q, pipeline, cfg)
            pkt = source.read(timeout_s=0.5)
            if pkt is None:
                if source.eos:
                    break
                continue
            processed["n"] += 1
            if pipeline is not None:
                try:
                    reads = pipeline.process(pkt.frame, pkt.ts_ms, pkt.grab_wall_ms)
                except Exception:  # noqa: BLE001 - one bad frame must not take the camera down
                    if time.monotonic() - last_error > 10.0:
                        log.exception("camera %s: frame processing failed", camera_id)
                        last_error = time.monotonic()
                    reads = []
                for read in reads:
                    out_q.put(read)
                if pkt.ts_ms - last_wm >= WATERMARK_EVERY_MS:
                    out_q.put(Watermark(camera_id, pkt.ts_ms))
                    last_wm = pkt.ts_ms
            elif pkt.ts_ms - last_snap >= int(cfg.settings.overview_snapshot_s * 1000):
                out_q.put(OverviewSnapshot(camera_id, gate.id, pkt.ts_ms, encode_jpeg(pkt.frame, 80, 1280)))
                last_snap = pkt.ts_ms
            if pipeline is not None and time.monotonic() - last_stats > STATS_LOG_EVERY_S:
                log.info("camera %s timings %s read_rate=%.2f", camera_id, pipeline.timer.summary(), pipeline.read_rate)
                last_stats = time.monotonic()
        if pipeline is not None:
            for read in pipeline.flush():
                out_q.put(read)
    finally:
        out_q.put(EndOfStream(camera_id))
        if hb is not None:
            hb.stop()
        source.close()
        if pipeline is not None:
            pipeline.recognizer.close()
        if backend is not None:
            backend.close()
        log.info("camera %s stopped", camera_id)


def _drain_control(control_q: "mp.Queue[Any]", pipeline: CameraPipeline | None, cfg: ServiceConfig) -> None:
    while True:
        try:
            msg = control_q.get_nowait()
        except queue.Empty:
            return
        if isinstance(msg, ConfigUpdate) and pipeline is not None:
            from .config import GateConfig, Settings

            cfg.settings = Settings.model_validate(msg.settings)
            pipeline.update_settings(cfg, GateConfig.model_validate(msg.gate))


def aggregator_main(
    cfg_data: dict[str, Any],
    gate_id: str,
    in_q: "mp.Queue[Any]",
    stop: MpEvent,
    done: MpEvent,
    anpr_ids: list[str],
    expected_cameras: list[str],
    log_level: str,
) -> None:
    setup_logging(log_level)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    cfg = ServiceConfig.model_validate(cfg_data)
    gate = cfg.gate(gate_id)
    agg = GateAggregator(gate, cfg.settings, cfg.plates.to_rules(), anpr_ids)
    outbox = Outbox(cfg.outbox_path)
    builder = EventBuilder(ImageStore(cfg.image_root))
    finished: set[str] = set()
    stop_seen: float | None = None
    log.info("gate %s aggregator started (ANPR cameras: %s)", gate_id, ",".join(anpr_ids))

    def publish(force: bool = False) -> None:
        for ev in agg.poll(force=force):
            payload = builder.build(ev)
            outbox.put(payload)
            log.info("gate=%s queued event %s %s plate=%s latency_ms=%d", gate_id, payload["event_id"],
                     payload["status"], payload["plate"], payload["latency_ms"])

    try:
        while True:
            try:
                msg = in_q.get(timeout=0.05)
            except queue.Empty:
                msg = None
            while msg is not None:
                if isinstance(msg, CameraRead):
                    agg.add_read(msg)
                elif isinstance(msg, Watermark):
                    agg.watermark(msg.camera_id, msg.ts_ms)
                elif isinstance(msg, OverviewSnapshot):
                    agg.add_overview(msg)
                elif isinstance(msg, EndOfStream):
                    agg.end_of_stream(msg.camera_id)
                    finished.add(msg.camera_id)
                elif isinstance(msg, ConfigUpdate):
                    from .config import GateConfig, Settings

                    agg.update_config(GateConfig.model_validate(msg.gate), Settings.model_validate(msg.settings))
                try:
                    msg = in_q.get_nowait()
                except queue.Empty:
                    msg = None
            publish()
            if expected_cameras and finished.issuperset(expected_cameras):
                publish(force=True)
                break
            if stop.is_set():
                stop_seen = stop_seen or time.monotonic()
                # Give workers a moment to flush their last reads, then emit everything pending.
                if time.monotonic() - stop_seen > (5.0 if expected_cameras else 1.0):
                    publish(force=True)
                    break
    finally:
        outbox.close()
        done.set()
        log.info("gate %s aggregator stopped: %s", gate_id, vars(agg.stats))


def build_sink(cfg: ServiceConfig) -> tuple[EventSink | None, BackendClient | None]:
    backend = BackendClient(cfg.backend) if cfg.backend.url else None
    jsonl = JsonlSink(cfg.emitter.events_jsonl) if cfg.emitter.events_jsonl else None
    if backend is not None and jsonl is not None:
        return TeeSink(backend, jsonl), backend
    if backend is not None:
        return backend, backend
    return jsonl, None


def emitter_main(
    cfg_data: dict[str, Any],
    stop: MpEvent,
    aggregators_done: list[MpEvent],
    exit_when_done: bool,
    drain_timeout_s: float,
    log_level: str,
) -> None:
    setup_logging(log_level)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    cfg = ServiceConfig.model_validate(cfg_data)
    outbox = Outbox(cfg.outbox_path)
    sink, _backend = build_sink(cfg)
    if sink is None:
        log.warning("no backend.url and no emitter.events_jsonl: events stay in the outbox %s", cfg.outbox_path)
        while not stop.is_set() and not (exit_when_done and all(e.is_set() for e in aggregators_done)):
            time.sleep(0.2)
        outbox.close()
        return
    emitter = Emitter(outbox, sink, cfg.emitter.retry_initial_s, cfg.emitter.retry_max_s)
    log.info("emitter started -> %s", cfg.backend.url or cfg.emitter.events_jsonl)
    try:
        if not exit_when_done:
            emitter.run(should_stop=stop.is_set)
            emitter.drain(deadline_s=2.0)  # best effort; the rest stays durable in the outbox
            return
        all_done_at: float | None = None
        while not stop.is_set():
            res = emitter.deliver_once()
            if res:
                continue
            if all(e.is_set() for e in aggregators_done):
                if outbox.depth() == 0:
                    break
                all_done_at = all_done_at or time.monotonic()
                if time.monotonic() - all_done_at > drain_timeout_s:
                    log.warning("drain timeout: %d events remain in the outbox", outbox.depth())
                    break
            head = outbox.head() if res is False else None
            wait = 0.05 if head is None else (head.next_attempt_ms - now_ms()) / 1000.0
            time.sleep(min(1.0, max(0.02, wait)))
    finally:
        log.info("emitter stopped: delivered=%d queued=%d", emitter.delivered, outbox.depth())
        sink.close()
        outbox.close()


# ------------------------------------------------------------------------ supervisor
class Supervisor:
    """Starts and watches all processes; refreshes config from the backend."""

    def __init__(
        self,
        cfg: ServiceConfig,
        replay_videos: dict[str, str] | None = None,
        realtime: bool = False,
        loop: bool = False,
        log_level: str = "INFO",
        drain_timeout_s: float = 30.0,
    ) -> None:
        self.cfg = cfg
        self.replay_videos = replay_videos
        self.realtime = realtime
        self.loop = loop
        self.log_level = log_level
        self.drain_timeout_s = drain_timeout_s
        self.ctx = mp.get_context("spawn")
        self.stop = self.ctx.Event()
        self.start_epoch_ms = now_ms()
        self._cams: dict[str, BaseProcess] = {}
        self._control: dict[str, Any] = {}
        self._gate_q: dict[str, Any] = {}
        self._aggs: dict[str, BaseProcess] = {}
        self._agg_done: dict[str, MpEvent] = {}
        self._emitter: BaseProcess | None = None
        self._restarts: dict[str, int] = {}

    @property
    def replay_mode(self) -> bool:
        return self.replay_videos is not None

    def _camera_plan(self) -> dict[str, ReplaySpec | None]:
        """camera_id -> replay spec (None = live RTSP) for every camera to run."""
        plan: dict[str, ReplaySpec | None] = {}
        for _gate, cam in self.cfg.all_cameras():
            if not cam.enabled:
                continue
            if self.replay_videos is not None:
                path = self.replay_videos.get(cam.id)
                if path:
                    plan[cam.id] = ReplaySpec(path, self.start_epoch_ms, self.loop, self.realtime)
            elif cam.rtsp_url or cam.gstreamer_pipeline:
                plan[cam.id] = None
            elif cam.replay_file:
                plan[cam.id] = ReplaySpec(cam.replay_file, self.start_epoch_ms, cam.replay_loop, True)
        return plan

    def _start_camera(self, camera_id: str, spec: ReplaySpec | None) -> None:
        gate, _cam = self.cfg.camera(camera_id)
        ctl = self._control.setdefault(camera_id, self.ctx.Queue())
        p = self.ctx.Process(
            target=camera_worker_main,
            name=f"cam-{camera_id}",
            args=(self.cfg.model_dump(mode="json"), camera_id, self._gate_q[gate.id], ctl, self.stop, spec,
                  self.log_level),
            daemon=True,
        )
        p.start()
        self._cams[camera_id] = p

    def run(self) -> int:
        plan = self._camera_plan()
        if not plan:
            log.error("no cameras to run (check rtsp_url / replay files)")
            return 2
        cfg_data = self.cfg.model_dump(mode="json")
        gates = sorted({self.cfg.camera(cid)[0].id for cid in plan})
        for gid in gates:
            gate = self.cfg.gate(gid)
            self._gate_q[gid] = self.ctx.Queue(maxsize=10000)
            done = self.ctx.Event()
            self._agg_done[gid] = done
            anpr_ids = [c.id for c in gate.anpr_cameras() if c.id in plan]
            expected = [c.id for c in gate.cameras if c.id in plan] if self.replay_mode and not self.loop else []
            p = self.ctx.Process(
                target=aggregator_main,
                name=f"agg-{gid}",
                args=(cfg_data, gid, self._gate_q[gid], self.stop, done, anpr_ids, expected, self.log_level),
                daemon=True,
            )
            p.start()
            self._aggs[gid] = p
        exit_when_done = self.replay_mode and not self.loop
        self._emitter = self.ctx.Process(
            target=emitter_main,
            name="emitter",
            args=(cfg_data, self.stop, list(self._agg_done.values()), exit_when_done, self.drain_timeout_s,
                  self.log_level),
            daemon=True,
        )
        self._emitter.start()
        for cid, spec in plan.items():
            self._start_camera(cid, spec)

        interrupted = {"flag": False}

        def _on_signal(signum: int, _frame: Any) -> None:
            log.info("signal %d received: shutting down", signum)
            interrupted["flag"] = True
            self.stop.set()

        signal.signal(signal.SIGTERM, _on_signal)
        signal.signal(signal.SIGINT, _on_signal)

        refresh_s = self.cfg.backend.config_refresh_s
        last_refresh = time.monotonic()
        backend = BackendClient(self.cfg.backend) if (self.cfg.backend.url and not self.replay_mode) else None
        try:
            while True:
                time.sleep(0.5)
                if exit_when_done and self._emitter is not None and not self._emitter.is_alive():
                    break
                if self.stop.is_set():
                    break
                if not self.replay_mode:
                    self._watchdog(plan)
                if backend is not None and time.monotonic() - last_refresh >= refresh_s:
                    last_refresh = time.monotonic()
                    self._refresh(backend, plan)
        finally:
            self.stop.set()
            for proc in [*self._cams.values(), *self._aggs.values()]:
                proc.join(timeout=10)
            if self._emitter is not None:
                self._emitter.join(timeout=self.drain_timeout_s + 5)
            for proc in [*self._cams.values(), *self._aggs.values(), self._emitter]:
                if proc is not None and proc.is_alive():
                    proc.terminate()
            if backend is not None:
                backend.close()
        return 130 if interrupted["flag"] else 0

    def _watchdog(self, plan: dict[str, ReplaySpec | None]) -> None:
        for cid, p in list(self._cams.items()):
            if p.is_alive():
                continue
            n = self._restarts.get(cid, 0) + 1
            self._restarts[cid] = n
            log.warning("camera worker %s exited (code %s); restart #%d", cid, p.exitcode, n)
            self._start_camera(cid, plan.get(cid))
        for gid, p in list(self._aggs.items()):
            if not p.is_alive():
                log.error("aggregator for gate %s died (code %s); stopping service for restart", gid, p.exitcode)
                self.stop.set()
        if self._emitter is not None and not self._emitter.is_alive():
            log.error("emitter died (code %s); stopping service for restart", self._emitter.exitcode)
            self.stop.set()

    def _refresh(self, backend: BackendClient, plan: dict[str, ReplaySpec | None]) -> None:
        remote = backend.fetch_config()
        if remote is None:
            return
        try:
            new_cfg = apply_remote_config(self.cfg, remote)
        except ValueError as exc:
            log.warning("ignoring invalid remote config: %s", exc)
            return
        old = self.cfg
        self.cfg = new_cfg
        settings_changed = old.settings != new_cfg.settings
        for gate in new_cfg.gates:
            try:
                old_gate = old.gate(gate.id)
            except KeyError:
                log.info("new gate %s in remote config (restart the service to start it)", gate.id)
                continue
            if gate.id in self._gate_q and (gate.direction != old_gate.direction or settings_changed
                                            or gate.name != old_gate.name):
                self._gate_q[gate.id].put(ConfigUpdate(gate.model_dump(mode="json"),
                                                       new_cfg.settings.model_dump(mode="json")))
            for cam in gate.cameras:
                if cam.id not in self._cams:
                    continue
                try:
                    _g, old_cam = old.camera(cam.id)
                except KeyError:
                    continue
                if cam != old_cam:
                    log.info("camera %s config changed: restarting worker", cam.id)
                    p = self._cams[cam.id]
                    p.terminate()
                    p.join(timeout=5)
                    self._start_camera(cam.id, plan.get(cam.id))
                elif settings_changed:
                    self._control[cam.id].put(ConfigUpdate(gate.model_dump(mode="json"),
                                                           new_cfg.settings.model_dump(mode="json")))
