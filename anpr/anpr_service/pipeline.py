"""Per-camera recognition pipeline.

frame -> vehicle detection/classification -> tracking -> plate association
-> quality gate (occlusion, sharpness, size) -> OCR (row-aware) -> per-track
multi-frame voting -> capture-line crossing (direction) -> ``CameraRead``.

The pipeline is synchronous and single-threaded; one instance runs inside
each camera worker process.  It holds no network or disk state, which keeps
it trivially testable and usable from the replay runner and the evaluator.
"""

from __future__ import annotations

import logging
import uuid
from collections import Counter, deque
from dataclasses import dataclass, field

import cv2
import numpy as np

from .config import CameraConfig, GateConfig, ServiceConfig
from .geometry import (
    Point,
    box_anchor,
    box_center,
    covered_fraction,
    expand_box,
    point_in_polygon,
    resolve_points,
    segment_crossing,
    travel_sign,
)
from .imaging import annotate, encode_jpeg, sharpness
from .metrics import StageTimer
from .plates import is_valid
from .recognizers.base import PlateRecognizer
from .tracker import SortTracker, TrackerParams
from .types import Box, CameraRead, ReadStatus, VehicleClass, VehicleObservation
from .voting import PlateVoter, VoteResult

log = logging.getLogger(__name__)

MIN_ROW_HEIGHT_PX = 13.0  # below this a plate row is too small to vote on
REF_ROW_HEIGHT_PX = 28.0  # rows this tall get full voting weight


@dataclass
class _TrackCtx:
    track_id: int
    voter: PlateVoter
    first_point: Point | None = None
    last_point: Point | None = None
    hits: int = 0
    classes: Counter[VehicleClass] = field(default_factory=Counter)
    crossed: bool = False
    finalized: bool = False
    cross_ts: int = 0
    cross_wall: int = 0
    travel: int = 1
    lateral: float = 0.5
    best_score: float = -1.0
    best_crop: np.ndarray | None = None
    best_frame: np.ndarray | None = None
    best_vehicle_box: Box | None = None
    best_plate_box: Box | None = None
    cross_frame: np.ndarray | None = None
    cross_box: Box | None = None
    skipped_occluded: int = 0
    skipped_quality: int = 0


class CameraPipeline:
    def __init__(
        self,
        cfg: ServiceConfig,
        gate: GateConfig,
        camera: CameraConfig,
        recognizer: PlateRecognizer,
    ) -> None:
        self.cfg = cfg
        self.gate = gate
        self.camera = camera
        self.recognizer = recognizer
        self.rules = cfg.plates.to_rules()
        p = cfg.pipeline
        self.tracker = SortTracker(
            TrackerParams(
                iou_threshold=p.tracker.iou_threshold,
                max_center_distance=p.tracker.max_center_distance,
                max_age=p.tracker.max_age,
                min_hits=p.tracker.min_hits,
            )
        )
        self.timer = StageTimer()
        self.frame_index = 0
        self._tracks: dict[int, _TrackCtx] = {}
        self._roi: list[Point] | None = None
        self._roi_mask: np.ndarray | None = None
        self._line: tuple[Point, Point] | None = None
        self._size: tuple[int, int] | None = None
        self.recent_status: deque[ReadStatus] = deque(maxlen=100)

    # ----------------------------------------------------------- geometry
    def _init_geometry(self, frame: np.ndarray) -> None:
        h, w = frame.shape[:2]
        self._size = (w, h)
        self._roi = resolve_points(self.camera.roi, w, h)
        if self._roi:
            mask = np.zeros((h, w), dtype=np.uint8)
            cv2.fillPoly(mask, [np.array(self._roi, dtype=np.int32)], 255)
            self._roi_mask = mask
        line = resolve_points(self.camera.capture_line, w, h)
        if not line or len(line) != 2:
            # Default: horizontal line across the frame at 60% height.
            line = [(0.0, 0.6 * h), (float(w), 0.6 * h)]
            log.warning("camera %s has no capture_line; using default %s", self.camera.id, line)
        self._line = (line[0], line[1])

    @property
    def read_rate(self) -> float:
        if not self.recent_status:
            return 1.0
        return sum(1 for s in self.recent_status if s == ReadStatus.READ) / len(self.recent_status)

    def update_settings(self, cfg: ServiceConfig, gate: GateConfig) -> None:
        self.cfg = cfg
        self.gate = gate
        self.rules = cfg.plates.to_rules()

    # ------------------------------------------------------------ process
    def process(self, frame: np.ndarray, ts_ms: int, grab_wall_ms: int) -> list[CameraRead]:
        if self._size is None or (frame.shape[1], frame.shape[0]) != self._size:
            self._init_geometry(frame)
        self.frame_index += 1
        p = self.cfg.pipeline
        if p.process_every_n > 1 and (self.frame_index - 1) % p.process_every_n:
            return []
        with self.timer.stage("detect"):
            observations = self.recognizer.analyze(frame, self._roi_mask)
        if self._roi:
            observations = [o for o in observations if point_in_polygon(box_center(o.box), self._roi)]
        with self.timer.stage("track"):
            updates = self.tracker.update([o.box for o in observations])
        reads: list[CameraRead] = []
        with self.timer.stage("plates"):
            for u in updates:
                ctx = self._tracks.get(u.track_id)
                if ctx is None:
                    ctx = self._tracks[u.track_id] = _TrackCtx(
                        track_id=u.track_id,
                        voter=PlateVoter(self.rules, min_votes=p.min_votes, max_votes=p.max_votes),
                    )
                if u.det_index is None:
                    continue  # coasting through an occlusion: nothing observed this frame
                obs = observations[u.det_index]
                ctx.hits += 1
                ctx.classes[obs.vehicle_class] += 1
                if obs.plate is not None and not ctx.finalized:
                    self._consider_plate(ctx, obs, observations, frame)
                self._check_crossing(ctx, obs, frame, ts_ms, grab_wall_ms)

        with self.timer.stage("finalize"):
            post_ms = int(p.post_cross_s * 1000)
            for ctx in list(self._tracks.values()):
                if not ctx.crossed or ctx.finalized:
                    continue
                result = ctx.voter.result()
                confident = (
                    result.best is not None
                    and ctx.voter.n_votes >= p.min_votes
                    and result.best.confidence >= self.cfg.settings.min_confidence
                    and is_valid(result.best.plate, self.rules)
                )
                if confident or ts_ms - ctx.cross_ts >= post_ms:
                    reads.append(self._finalize(ctx, result))
            for t in self.tracker.removed:
                ctx = self._tracks.pop(t.track_id, None)
                if ctx is not None and ctx.crossed and not ctx.finalized:
                    reads.append(self._finalize(ctx, ctx.voter.result()))
            # Forget finalized tracks that the tracker no longer knows about.
            live = {u.track_id for u in updates}
            for tid in [tid for tid, c in self._tracks.items() if tid not in live]:
                del self._tracks[tid]
        return reads

    def flush(self) -> list[CameraRead]:
        """End of stream: finalize every crossed-but-pending track."""
        out = [
            self._finalize(ctx, ctx.voter.result())
            for ctx in self._tracks.values()
            if ctx.crossed and not ctx.finalized
        ]
        self._tracks.clear()
        return out

    # ------------------------------------------------------------ helpers
    def _consider_plate(
        self, ctx: _TrackCtx, obs: VehicleObservation, others: list[VehicleObservation], frame: np.ndarray
    ) -> None:
        p = self.cfg.pipeline
        assert obs.plate is not None
        pb = obs.plate.box
        pw, ph = pb[2] - pb[0], pb[3] - pb[1]
        if pw < p.min_plate_width_px:
            ctx.skipped_quality += 1
            return
        # Occlusion: a nearer vehicle (lower in a rear view) covering the plate.
        occl = 0.0
        for o in others:
            if o is obs:
                continue
            if _is_nearer(o.box, obs.box):
                occl = max(occl, covered_fraction(pb, o.box))
        if occl > p.max_plate_occlusion:
            ctx.skipped_occluded += 1
            return
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = expand_box(pb, 0.04, w, h)
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return
        sharp = sharpness(crop)
        if sharp < p.min_sharpness:
            ctx.skipped_quality += 1
            return
        read = obs.plate.read
        if read is None:
            with self.timer.stage("ocr"):
                read = self.recognizer.read_plate(crop)
        if read is None or not read.text:
            return
        row_h = ph / max(1, read.rows)
        if row_h < MIN_ROW_HEIGHT_PX:
            ctx.skipped_quality += 1
            return
        size_w = min(1.0, row_h / REF_ROW_HEIGHT_PX)
        sharp_w = min(1.0, sharp / (4.0 * max(p.min_sharpness, 1.0)))
        weight = size_w * (0.5 + 0.5 * sharp_w) * (1.0 - occl)
        ctx.voter.add(read, weight)
        score = weight * read.confidence
        if score > ctx.best_score:
            ctx.best_score = score
            ctx.best_crop = crop.copy()
            ctx.best_frame = frame.copy()
            ctx.best_vehicle_box = obs.box
            ctx.best_plate_box = pb

    def _check_crossing(
        self, ctx: _TrackCtx, obs: VehicleObservation, frame: np.ndarray, ts_ms: int, grab_wall_ms: int
    ) -> None:
        point = box_anchor(obs.box, self.cfg.pipeline.track_anchor)
        if ctx.first_point is None:
            ctx.first_point = point
        if (
            not ctx.crossed
            and ctx.last_point is not None
            and ctx.hits >= self.cfg.pipeline.min_track_hits
            and self._line is not None
        ):
            cr = segment_crossing(ctx.last_point, point, self._line[0], self._line[1])
            if cr is not None:
                motion = (point[0] - ctx.first_point[0], point[1] - ctx.first_point[1])
                if motion == (0.0, 0.0):
                    motion = (point[0] - ctx.last_point[0], point[1] - ctx.last_point[1])
                ctx.crossed = True
                ctx.cross_ts = ts_ms
                ctx.cross_wall = grab_wall_ms
                ctx.travel = travel_sign(motion, self.camera.in_vector)
                ctx.lateral = self._lateral(cr.point[0])
                if ctx.best_frame is None:
                    ctx.cross_frame = frame.copy()
                    ctx.cross_box = obs.box
                log.debug(
                    "camera=%s track=%d crossed ts=%d travel=%+d lateral=%.2f votes=%d",
                    self.camera.id, ctx.track_id, ts_ms, ctx.travel, ctx.lateral, ctx.voter.n_votes,
                )
        ctx.last_point = point

    def _lateral(self, x: float) -> float:
        w = self._size[0] if self._size else 1
        s0, s1 = self.camera.effective_gate_span
        return float(min(1.0, max(0.0, s0 + (x / float(w)) * (s1 - s0))))

    def _finalize(self, ctx: _TrackCtx, result: VoteResult) -> CameraRead:
        p = self.cfg.pipeline
        best = result.best
        is_read = (
            best is not None
            and best.confidence >= self.cfg.settings.min_confidence
            and is_valid(best.plate, self.rules)
        )
        status = ReadStatus.READ if is_read else ReadStatus.UNREAD
        plate = best.plate if (is_read and best is not None) else None
        plate_jpeg = encode_jpeg(ctx.best_crop, p.jpeg_quality) if ctx.best_crop is not None else None
        frame = ctx.best_frame if ctx.best_frame is not None else ctx.cross_frame
        frame_jpeg = None
        if frame is not None:
            vbox = ctx.best_vehicle_box if ctx.best_frame is not None else ctx.cross_box
            pbox = ctx.best_plate_box if ctx.best_frame is not None else None
            if p.annotate_full_frame:
                frame = annotate(frame, vbox, pbox, plate or "UNREAD")
            frame_jpeg = encode_jpeg(frame, p.jpeg_quality, p.full_frame_max_width)
        vclass = ctx.classes.most_common(1)[0][0] if ctx.classes else VehicleClass.OTHER
        ctx.finalized = True
        ctx.best_crop = ctx.best_frame = ctx.cross_frame = None
        self.recent_status.append(status)
        read = CameraRead(
            read_id=str(uuid.uuid4()),
            camera_id=self.camera.id,
            gate_id=self.gate.id,
            track_id=ctx.track_id,
            ts_ms=ctx.cross_ts,
            grab_wall_ms=ctx.cross_wall,
            travel_sign=ctx.travel,
            vehicle_class=vclass,
            status=status,
            plate=plate,
            confidence=round(best.confidence, 4) if best else 0.0,
            candidates=result.candidates,
            lateral=ctx.lateral,
            n_votes=result.n_votes,
            plate_jpeg=plate_jpeg,
            frame_jpeg=frame_jpeg,
            timings_ms={k: v["mean_ms"] for k, v in self.timer.summary().items()},
        )
        log.info(
            "camera=%s track=%d %s plate=%s conf=%.2f votes=%d occluded_skips=%d lateral=%.2f",
            self.camera.id, ctx.track_id, status.value, plate, read.confidence, result.n_votes,
            ctx.skipped_occluded, ctx.lateral,
        )
        return read


def _is_nearer(a: Box, b: Box) -> bool:
    """In a rear view, is vehicle ``a`` nearer to the camera than ``b``?"""
    if a[3] > b[3] + 2:
        return True
    if abs(a[3] - b[3]) <= 2:
        return (a[3] - a[1]) > 1.1 * (b[3] - b[1])
    return False
