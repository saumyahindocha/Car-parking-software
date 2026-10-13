"""Local open-source pipeline on ONNX Runtime (CUDA / TensorRT when available).

* Detector: a YOLO-style ONNX export with vehicle classes and a plate class
  (``detector_classes`` maps class index -> BIKE | CAR | OTHER | PLATE), or
  vehicle-only plus an optional second-stage plate detector run on vehicle
  crops.  Use an Apache/MIT-licensed architecture (YOLOX, RT-DETR, ...) or
  hold an Ultralytics commercial licence - see docs/ANPR.md.
* OCR: a CRNN + CTC model (``tools/train_ocr.py`` exports a compatible one)
  run per text row: two-line plates are split by horizontal projection and
  the rows are read top to bottom and concatenated.

``onnxruntime`` is imported lazily so the service runs without it when this
engine is not selected.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, Protocol

import cv2
import numpy as np

from ..config import OnnxConfig
from ..rows import split_rows
from ..types import OcrResult, PlateObservation, VehicleClass, VehicleObservation
from . import yolo
from .base import PlateRecognizer, RecognizerUnavailable, associate_plates
from .ctc import ctc_greedy_decode

log = logging.getLogger(__name__)


class _Input(Protocol):
    name: str
    shape: Any


class InferenceSession(Protocol):
    def get_inputs(self) -> list[_Input]: ...

    def run(self, output_names: Any, input_feed: dict[str, np.ndarray]) -> list[np.ndarray]: ...


SessionFactory = Callable[[str], InferenceSession]


def ort_session_factory(providers: list[str]) -> SessionFactory:
    try:
        import onnxruntime as ort  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise RecognizerUnavailable(
            "onnxruntime is not installed: pip install onnxruntime-gpu (edge GPU) or onnxruntime (CPU)"
        ) from exc
    available = set(ort.get_available_providers())
    chosen = [p for p in providers if p in available] or ["CPUExecutionProvider"]
    log.info("onnxruntime %s providers: %s", ort.__version__, chosen)

    def make(path: str) -> InferenceSession:
        if not os.path.exists(path):
            raise RecognizerUnavailable(f"model file not found: {path}")
        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        return ort.InferenceSession(path, sess_options=opts, providers=chosen)

    return make


_CLASS_MAP = {"BIKE": VehicleClass.BIKE, "CAR": VehicleClass.CAR, "OTHER": VehicleClass.OTHER}


class LocalOnnxRecognizer(PlateRecognizer):
    name = "onnx"

    def __init__(self, cfg: OnnxConfig, session_factory: SessionFactory | None = None) -> None:
        self.cfg = cfg
        factory = session_factory or ort_session_factory(cfg.providers)
        self.detector = factory(cfg.detector_model)
        self.plate_detector = factory(cfg.plate_detector_model) if cfg.plate_detector_model else None
        self.ocr = factory(cfg.ocr_model)
        self._det_input = self.detector.get_inputs()[0].name
        self._ocr_input = self.ocr.get_inputs()[0].name
        self._classes = {int(k): str(v).upper() for k, v in cfg.detector_classes.items()}

    # ------------------------------------------------------------ detection
    def _detect(self, session: InferenceSession, img: np.ndarray, size: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        h, w = img.shape[:2]
        lb, scale, pad = yolo.letterbox(img, size)
        name = session.get_inputs()[0].name
        raw = session.run(None, {name: yolo.to_nchw(lb)})[0]
        boxes, scores, cls = yolo.decode(raw, self.cfg.conf_threshold, self.cfg.detector_format)
        keep = yolo.batched_nms(boxes, scores, cls, self.cfg.nms_iou)
        boxes, scores, cls = boxes[keep], scores[keep], cls[keep]
        return yolo.unletterbox(boxes, scale, pad, w, h), scores, cls

    def analyze(self, frame: np.ndarray, roi_mask: np.ndarray | None = None) -> list[VehicleObservation]:
        boxes, scores, cls = self._detect(self.detector, frame, self.cfg.detector_input_size)
        vehicles: list[tuple[tuple[float, float, float, float], VehicleClass, float]] = []
        plates: list[PlateObservation] = []
        for b, s, c in zip(boxes.tolist(), scores.tolist(), cls.tolist()):
            label = self._classes.get(int(c), "OTHER")
            box = (b[0], b[1], b[2], b[3])
            if label == "PLATE":
                plates.append(PlateObservation(box=box, score=float(s)))
            elif label in _CLASS_MAP:
                vehicles.append((box, _CLASS_MAP[label], float(s)))
        if self.plate_detector is not None:
            h, w = frame.shape[:2]
            for vb, _vc, _vs in vehicles:
                x1, y1, x2, y2 = int(vb[0]), int(vb[1]), int(np.ceil(vb[2])), int(np.ceil(vb[3]))
                crop = frame[max(0, y1) : min(h, y2), max(0, x1) : min(w, x2)]
                if crop.size == 0:
                    continue
                pb, ps, _pc = self._detect(self.plate_detector, crop, self.cfg.plate_detector_input_size)
                for b, s in zip(pb.tolist(), ps.tolist()):
                    plates.append(PlateObservation(box=(b[0] + x1, b[1] + y1, b[2] + x1, b[3] + y1), score=float(s)))
        assigned = associate_plates([v[0] for v in vehicles], plates)
        return [
            VehicleObservation(box=vb, vehicle_class=vc, score=vs, plate=pl)
            for (vb, vc, vs), pl in zip(vehicles, assigned)
        ]

    # ------------------------------------------------------------------ OCR
    def _prep_row(self, row: np.ndarray) -> np.ndarray:
        c = self.cfg
        img = cv2.cvtColor(row, cv2.COLOR_BGR2GRAY) if c.ocr_channels == 1 else cv2.cvtColor(row, cv2.COLOR_BGR2RGB)
        h, w = img.shape[:2]
        new_w = min(c.ocr_input_width, max(1, int(round(w * c.ocr_input_height / float(h)))))
        img = cv2.resize(img, (new_w, c.ocr_input_height), interpolation=cv2.INTER_CUBIC)
        canvas_shape = (c.ocr_input_height, c.ocr_input_width) + (() if c.ocr_channels == 1 else (3,))
        canvas = np.full(canvas_shape, 255 if c.ocr_channels == 1 else 0, dtype=np.uint8)
        canvas[:, :new_w] = img
        x = (canvas.astype(np.float32) / 255.0 - c.ocr_mean) / c.ocr_std
        if c.ocr_channels == 1:
            return x[None, None]
        return np.ascontiguousarray(x.transpose(2, 0, 1)[None])

    def read_plate(self, crop: np.ndarray) -> OcrResult | None:
        if crop is None or crop.size == 0:
            return None
        rows = split_rows(crop)
        text = ""
        confs: list[float] = []
        for row in rows:
            if row.size == 0 or min(row.shape[:2]) < 4:
                continue
            out = self.ocr.run(None, {self._ocr_input: self._prep_row(row)})[0]
            t, c = ctc_greedy_decode(out, self.cfg.ocr_alphabet, self.cfg.ocr_blank_index)
            text += t
            confs += c
        if not text:
            return None
        return OcrResult(text=text, char_confs=confs, rows=len(rows))
