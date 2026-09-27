"""Our own plate recogniser: two small networks trained on computer-generated plates.

* **Vehicles** come from motion (background subtraction), exactly as in
  :class:`ClassicalRecognizer` - a gate camera watches a fixed scene, so anything
  that moves through the lane is a vehicle and needs no learned detector.
* **Plate finder** (``models/plate_finder.onnx``): a fully convolutional
  CenterNet-style network that marks plate centres and sizes on the whole frame.
* **Plate reader** (``models/plate_reader.onnx``): reads a plate crop (one or two
  rows) in one pass - 11 character slots, each a softmax over 0-9 A-Z plus
  "empty" - so it gives per-character confidences and alternatives for voting.

Both are trained by ``anpr/training`` on synthetic Indian plates (fonts, HSRP /
old / commercial styles, dirt, glare, IR night, blur, perspective) and are
meant to be fine-tuned on a week of site footage (docs/ANPR.md).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import cv2
import numpy as np

from ..config import ClassicalConfig, TrainedConfig
from ..types import Box, OcrResult, PlateObservation
from .base import RecognizerUnavailable
from .classical import ClassicalRecognizer

ANPR_ROOT = Path(__file__).resolve().parents[2]


def resolve_model(path: str) -> Path:
    p = Path(os.path.expandvars(path))
    if p.is_absolute() or p.exists():
        return p
    return ANPR_ROOT / p


def _session(path: Path, threads: int):
    try:
        import onnxruntime as ort
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise RecognizerUnavailable("onnxruntime is not installed (pip install onnxruntime)") from exc
    if not path.exists():
        raise RecognizerUnavailable(f"model file not found: {path}")
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = max(1, threads)
    opts.inter_op_num_threads = 1
    return ort.InferenceSession(str(path), sess_options=opts, providers=["CPUExecutionProvider"])


def _meta(path: Path) -> dict:
    j = path.with_suffix(".json")
    return json.loads(j.read_text()) if j.exists() else {}


class PlateReader:
    """Whole-plate slot classifier (see ``training/train_reader.py``)."""

    def __init__(self, path: Path, threads: int = 2) -> None:
        self.sess = _session(path, threads)
        meta = _meta(path)
        self.alphabet: str = meta.get("alphabet", "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ")
        self.empty: int = int(meta.get("empty_index", len(self.alphabet)))
        shape = self.sess.get_inputs()[0].shape
        self.h = int(meta.get("input_height", shape[2] if isinstance(shape[2], int) else 64))
        self.w = int(meta.get("input_width", shape[3] if isinstance(shape[3], int) else 128))

    def prepare(self, crop: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
        interp = cv2.INTER_AREA if gray.shape[1] > self.w else cv2.INTER_LINEAR
        return cv2.resize(gray, (self.w, self.h), interpolation=interp).astype(np.float32)

    def probs(self, crops: list[np.ndarray]) -> np.ndarray:
        x = np.stack([self.prepare(c) for c in crops])[:, None]
        return self.sess.run(None, {"x": x})[0]  # [N, slots, classes]

    def decode(self, p: np.ndarray, n_alts: int = 3) -> OcrResult | None:
        chars: list[str] = []
        confs: list[float] = []
        alts: list[list[tuple[str, float]]] = []
        for slot in p:
            best = int(slot.argmax())
            if best == self.empty:
                continue
            order = [int(i) for i in np.argsort(-slot) if int(i) != self.empty][:n_alts]
            total = float(sum(slot[i] for i in range(len(slot)) if i != self.empty)) or 1.0
            chars.append(self.alphabet[best])
            confs.append(float(slot[best]))
            alts.append([(self.alphabet[i], float(slot[i]) / total) for i in order])
        if not chars:
            return None
        return OcrResult(text="".join(chars), char_confs=confs, rows=1, alternatives=alts)

    def read(self, crop: np.ndarray) -> OcrResult | None:
        res = self.decode(self.probs([crop])[0])
        if res is not None:
            res.rows = two_rows(crop)
        return res


def two_rows(crop: np.ndarray) -> int:
    """1 or 2 text rows, from the plate's aspect ratio (two-row bike plates are ~2:1 or squarer)."""
    h, w = crop.shape[:2]
    return 2 if w < 2.6 * h else 1


class PlateFinder:
    """Plate detector on the whole frame (see ``training/train_finder.py``)."""

    def __init__(self, path: Path, width: int = 960, threshold: float = 0.35, threads: int = 2) -> None:
        self.sess = _session(path, threads)
        meta = _meta(path)
        self.stride = int(meta.get("stride", 4))
        self.width = max(64, width // 32 * 32)
        self.threshold = threshold

    def find(self, frame: np.ndarray, topk: int = 20) -> list[tuple[float, float, float, float, float]]:
        fh, fw = frame.shape[:2]
        scale = self.width / float(fw)
        h = max(32, int(round(fh * scale / 32)) * 32)
        sx, sy = self.width / float(fw), h / float(fh)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        small = cv2.resize(gray, (self.width, h), interpolation=cv2.INTER_AREA if sx < 1 else cv2.INTER_LINEAR)
        hm, wh, off = self.sess.run(None, {"x": small.astype(np.float32)[None, None]})
        hm, wh, off = hm[0, 0], wh[0], off[0]
        pooled = cv2.dilate(hm, np.ones((3, 3), np.float32))
        ys, xs = np.nonzero((hm >= pooled) & (hm >= self.threshold))
        order = np.argsort(-hm[ys, xs])[:topk]
        s = self.stride
        out = []
        for k in order:
            y, x = int(ys[k]), int(xs[k])
            bw, bh = float(np.exp(wh[0, y, x])) * s, float(np.exp(wh[1, y, x])) * s
            cx, cy = (x + float(off[0, y, x])) * s, (y + float(off[1, y, x])) * s
            out.append(((cx - bw / 2) / sx, (cy - bh / 2) / sy, (cx + bw / 2) / sx, (cy + bh / 2) / sy, float(hm[y, x])))
        return merge_split_plates(out)


def merge_split_plates(boxes: list[tuple[float, float, float, float, float]]) -> list[tuple[float, float, float, float, float]]:
    """Join boxes that overlap on the same text line into one plate.

    A long one-row plate can light up two heatmap peaks (its left and right
    halves); two real plates never overlap each other, so any overlap on the
    same line means one plate.
    """
    out = sorted(boxes, key=lambda b: -b[4])
    merged = True
    while merged:
        merged = False
        for i in range(len(out)):
            for j in range(i + 1, len(out)):
                a, b = out[i], out[j]
                ix = min(a[2], b[2]) - max(a[0], b[0])
                iy = min(a[3], b[3]) - max(a[1], b[1])
                if ix <= 0 or iy <= 0.5 * min(a[3] - a[1], b[3] - b[1]):
                    continue
                out[i] = (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]), max(a[4], b[4]))
                del out[j]
                merged = True
                break
            if merged:
                break
    return out


class TrainedRecognizer(ClassicalRecognizer):
    name = "trained"

    def __init__(
        self,
        cfg: TrainedConfig | None = None,
        classical: ClassicalConfig | None = None,
        min_plate_width_px: int = 36,
    ) -> None:
        super().__init__(classical, min_plate_width_px)
        self.tcfg = cfg or TrainedConfig()
        t = self.tcfg
        self.finder = PlateFinder(resolve_model(t.finder_model), t.finder_width, t.finder_threshold, t.threads)
        self.reader = PlateReader(resolve_model(t.reader_model), t.threads)
        self._frame_plates: list[PlateObservation] | None = None

    def _plates_in_frame(self, frame: np.ndarray) -> list[PlateObservation]:
        # analyze() calls find_plates once per motion blob; run the finder once per frame.
        if self._frame_plates is not None:
            return self._frame_plates
        fh, fw = frame.shape[:2]
        plates = []
        for x1, y1, x2, y2, score in self.finder.find(frame):
            box = (max(0.0, x1), max(0.0, y1), min(float(fw), x2), min(float(fh), y2))
            if box[2] - box[0] < 8 or box[3] - box[1] < 4:
                continue
            if box[0] <= 1 or box[1] <= 1 or box[2] >= fw - 1 or box[3] >= fh - 1:
                continue  # cut off by the frame edge
            plates.append(PlateObservation(box=box, score=score))
        return plates

    def find_plates(self, frame: np.ndarray, box: Box) -> list[PlateObservation]:
        out = []
        for p in self._plates_in_frame(frame):
            cx, cy = (p.box[0] + p.box[2]) / 2, (p.box[1] + p.box[3]) / 2
            if box[0] <= cx <= box[2] and box[1] <= cy <= box[3]:
                out.append(p)
        return out

    def analyze(self, frame, roi_mask=None):
        self._frame_plates = self._plates_in_frame(frame)
        try:
            return self._analyze(frame, roi_mask)
        finally:
            self._frame_plates = None

    def _analyze(self, frame, roi_mask):
        vehicles = super().analyze(frame, roi_mask)
        if not self.tcfg.whole_frame_plates or self._frames <= self.cfg.warmup_frames:
            return vehicles
        # A plate with no motion blob around it (vehicle stopped long enough to melt
        # into the background, or a dark bike on dark asphalt) still gets reported.
        taken = {id(v.plate) for v in vehicles if v.plate is not None}
        h, w = frame.shape[:2]
        for p in self._plates_in_frame(frame):
            if id(p) in taken or p.score < max(0.5, self.tcfg.finder_threshold):
                continue
            if roi_mask is not None:
                cx, cy = int((p.box[0] + p.box[2]) / 2), int((p.box[1] + p.box[3]) / 2)
                if roi_mask[min(cy, roi_mask.shape[0] - 1), min(cx, roi_mask.shape[1] - 1)] == 0:
                    continue
            if any(_inside(p.box, v.box) for v in vehicles):
                continue
            vehicles.append(self._vehicle_from_plate(p, (w, h)))
        return vehicles

    def read_plate(self, crop: np.ndarray) -> OcrResult | None:
        if crop is None or crop.size == 0 or min(crop.shape[:2]) < 6:
            return None
        return self.reader.read(crop)


def _inside(plate: Box, vehicle: Box) -> bool:
    cx, cy = (plate[0] + plate[2]) / 2, (plate[1] + plate[3]) / 2
    return vehicle[0] <= cx <= vehicle[2] and vehicle[1] <= cy <= vehicle[3]
