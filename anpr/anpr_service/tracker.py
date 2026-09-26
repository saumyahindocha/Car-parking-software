"""SORT-style multi-object tracker: constant-velocity Kalman filter + Hungarian IoU matching.

Own implementation (no AGPL code).  State per track is
``[cx, cy, w, h, vx, vy, vw, vh]``; a detection is matched to a track by IoU
between the detection and the track's predicted box, with a fallback on
normalised centre distance so fast-moving / low-frame-rate objects are not
lost.  Tracks survive ``max_age`` frames without detections (occlusion by a
side-by-side vehicle) and keep their identity when they reappear.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .geometry import iou_matrix
from .hungarian import linear_assignment
from .types import Box


@dataclass
class TrackerParams:
    iou_threshold: float = 0.2
    max_center_distance: float = 0.6  # in units of the predicted box diagonal
    max_age: int = 12
    min_hits: int = 2
    size_change_limit: float = 2.5  # reject matches whose area changes more than this factor


class KalmanBoxFilter:
    """Linear Kalman filter over (cx, cy, w, h) with constant velocity."""

    def __init__(self, box: Box) -> None:
        cx, cy, w, h = _to_cxcywh(box)
        self.x = np.array([cx, cy, w, h, 0.0, 0.0, 0.0, 0.0], dtype=np.float64)
        self.F = np.eye(8)
        for i in range(4):
            self.F[i, i + 4] = 1.0
        self.H = np.zeros((4, 8))
        self.H[:4, :4] = np.eye(4)
        scale = max(w, h, 1.0)
        self.P = np.diag([scale, scale, scale, scale, 4 * scale, 4 * scale, scale, scale]) ** 1.0
        self._scale = scale
        self.age_since_update = 0

    def _process_noise(self) -> np.ndarray:
        s = max(self.x[2], self.x[3], 1.0)
        q_pos = (0.05 * s) ** 2
        q_vel = (0.02 * s) ** 2
        return np.diag([q_pos, q_pos, q_pos, q_pos, q_vel, q_vel, q_vel * 0.5, q_vel * 0.5])

    def _measurement_noise(self) -> np.ndarray:
        s = max(self.x[2], self.x[3], 1.0)
        r = (0.04 * s) ** 2
        return np.diag([r, r, r * 2, r * 2])

    def predict(self) -> Box:
        self.x = self.F @ self.x
        # Size must stay positive.
        self.x[2] = max(self.x[2], 1.0)
        self.x[3] = max(self.x[3], 1.0)
        self.P = self.F @ self.P @ self.F.T + self._process_noise()
        self.age_since_update += 1
        return self.box

    def update(self, box: Box) -> None:
        z = np.array(_to_cxcywh(box), dtype=np.float64)
        y = z - self.H @ self.x
        s = self.H @ self.P @ self.H.T + self._measurement_noise()
        k = self.P @ self.H.T @ np.linalg.inv(s)
        self.x = self.x + k @ y
        self.P = (np.eye(8) - k @ self.H) @ self.P
        self.age_since_update = 0

    @property
    def box(self) -> Box:
        cx, cy, w, h = self.x[:4]
        return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)

    @property
    def velocity(self) -> tuple[float, float]:
        return float(self.x[4]), float(self.x[5])


@dataclass
class Track:
    track_id: int
    kf: KalmanBoxFilter
    hits: int = 1
    age: int = 1
    time_since_update: int = 0
    last_box: Box = (0.0, 0.0, 0.0, 0.0)
    history: list[Box] = field(default_factory=list)

    @property
    def confirmed(self) -> bool:
        return self.hits >= 1 and self.time_since_update == 0


@dataclass
class TrackUpdate:
    track_id: int
    box: Box  # the observed box when matched, else the predicted box
    det_index: int | None  # index into this frame's detections, None when coasting
    hits: int
    time_since_update: int


class SortTracker:
    def __init__(self, params: TrackerParams | None = None) -> None:
        self.params = params or TrackerParams()
        self._tracks: list[Track] = []
        self._next_id = 1
        self.removed: list[Track] = []  # tracks removed during the last update()

    @property
    def tracks(self) -> list[Track]:
        return list(self._tracks)

    def update(self, detections: list[Box]) -> list[TrackUpdate]:
        """Advance one frame.  Returns updates for all live tracks.

        Tracks that were not matched this frame are returned with
        ``det_index=None`` (coasting on the Kalman prediction) until they
        exceed ``max_age``; removed tracks are exposed in ``self.removed``.
        """
        p = self.params
        predicted = np.array([t.kf.predict() for t in self._tracks], dtype=np.float64).reshape(-1, 4)
        dets = np.array(detections, dtype=np.float64).reshape(-1, 4)
        matches, unmatched_tracks, unmatched_dets = self._associate(predicted, dets)

        for ti, di in matches:
            t = self._tracks[ti]
            box = tuple(float(v) for v in dets[di])
            t.kf.update(box)  # type: ignore[arg-type]
            t.hits += 1
            t.time_since_update = 0
            t.last_box = box  # type: ignore[assignment]
            t.history.append(box)  # type: ignore[arg-type]
            if len(t.history) > 64:
                del t.history[0]
        for ti in unmatched_tracks:
            self._tracks[ti].time_since_update += 1
        for di in unmatched_dets:
            box = tuple(float(v) for v in dets[di])
            track = Track(track_id=self._next_id, kf=KalmanBoxFilter(box), last_box=box, history=[box])  # type: ignore[arg-type]
            self._next_id += 1
            self._tracks.append(track)
            matches.append((len(self._tracks) - 1, di))

        for t in self._tracks:
            t.age += 1

        det_for_track = {ti: di for ti, di in matches}
        self.removed = [t for t in self._tracks if t.time_since_update > p.max_age]
        out: list[TrackUpdate] = []
        keep: list[Track] = []
        for idx, t in enumerate(self._tracks):
            if t.time_since_update > p.max_age:
                continue
            keep.append(t)
            di = det_for_track.get(idx)
            box = t.last_box if di is not None else t.kf.box
            out.append(TrackUpdate(t.track_id, box, di, t.hits, t.time_since_update))
        self._tracks = keep
        return out

    def _associate(
        self, predicted: np.ndarray, dets: np.ndarray
    ) -> tuple[list[tuple[int, int]], list[int], list[int]]:
        p = self.params
        n_t, n_d = len(predicted), len(dets)
        if n_t == 0 or n_d == 0:
            return [], list(range(n_t)), list(range(n_d))
        ious = iou_matrix(predicted, dets)
        # Normalised centre distance as a secondary cue.
        pc = np.stack([(predicted[:, 0] + predicted[:, 2]) / 2, (predicted[:, 1] + predicted[:, 3]) / 2], axis=1)
        dc = np.stack([(dets[:, 0] + dets[:, 2]) / 2, (dets[:, 1] + dets[:, 3]) / 2], axis=1)
        diag = np.hypot(predicted[:, 2] - predicted[:, 0], predicted[:, 3] - predicted[:, 1])
        dist = np.linalg.norm(pc[:, None, :] - dc[None, :, :], axis=2) / np.maximum(diag[:, None], 1.0)
        area_p = np.maximum((predicted[:, 2] - predicted[:, 0]) * (predicted[:, 3] - predicted[:, 1]), 1.0)
        area_d = np.maximum((dets[:, 2] - dets[:, 0]) * (dets[:, 3] - dets[:, 1]), 1.0)
        ratio = np.maximum(area_p[:, None] / area_d[None, :], area_d[None, :] / area_p[:, None])

        gate_ok = ((ious >= p.iou_threshold) | (dist <= p.max_center_distance)) & (ratio <= p.size_change_limit)
        cost = 1.0 - ious + 0.5 * np.minimum(dist, 2.0)
        big = 1e6
        cost = np.where(gate_ok, cost, big)
        pairs = linear_assignment(cost)
        matches = [(r, c) for r, c in pairs if cost[r, c] < big]
        mt = {r for r, _ in matches}
        md = {c for _, c in matches}
        return (
            matches,
            [i for i in range(n_t) if i not in mt],
            [j for j in range(n_d) if j not in md],
        )


def _to_cxcywh(box: Box) -> tuple[float, float, float, float]:
    x1, y1, x2, y2 = box
    return (x1 + x2) / 2.0, (y1 + y2) / 2.0, max(x2 - x1, 1.0), max(y2 - y1, 1.0)
