"""Image helpers: JPEG encoding, sharpness, annotation and the shared image store."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import cv2
import numpy as np

from .types import Box

log = logging.getLogger(__name__)


def encode_jpeg(img: np.ndarray, quality: int = 88, max_width: int | None = None) -> bytes:
    if max_width and img.shape[1] > max_width:
        scale = max_width / float(img.shape[1])
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:
        raise ValueError("JPEG encoding failed")
    return buf.tobytes()


def sharpness(img: np.ndarray) -> float:
    """Variance of the Laplacian (higher = sharper)."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def annotate(frame: np.ndarray, vehicle: Box | None, plate: Box | None, label: str | None = None) -> np.ndarray:
    out = frame.copy()
    thick = max(2, frame.shape[1] // 640)
    if vehicle is not None:
        cv2.rectangle(out, (int(vehicle[0]), int(vehicle[1])), (int(vehicle[2]), int(vehicle[3])), (0, 200, 255), thick)
    if plate is not None:
        cv2.rectangle(out, (int(plate[0]), int(plate[1])), (int(plate[2]), int(plate[3])), (0, 255, 0), thick)
    if label and vehicle is not None:
        scale = max(0.6, frame.shape[1] / 1600)
        org = (int(vehicle[0]), max(20, int(vehicle[1]) - 8))
        cv2.putText(out, label, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thick + 2, cv2.LINE_AA)
        cv2.putText(out, label, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 255, 255), thick, cv2.LINE_AA)
    return out


class ImageStore:
    """Writes event JPEGs under ``IMAGE_ROOT/YYYY/MM/DD/<gate>/<event_id>_<kind>.jpg``.

    Paths returned are relative to the root (the backend shares the volume).
    Dates use the edge server's local time zone (``TZ=Asia/Kolkata`` in the
    container).  Files are written atomically (temp file + rename) so the
    backend never serves a half-written image.
    """

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)

    def relpath(self, gate_id: str, event_id: str, kind: str, ts_ms: int) -> str:
        t = time.localtime(ts_ms / 1000.0)
        safe_kind = "".join(c if c.isalnum() or c in "-_" else "_" for c in kind)
        return f"{t.tm_year:04d}/{t.tm_mon:02d}/{t.tm_mday:02d}/{gate_id}/{event_id}_{safe_kind}.jpg"

    def write(self, gate_id: str, event_id: str, kind: str, ts_ms: int, jpeg: bytes) -> str:
        rel = self.relpath(gate_id, event_id, kind, ts_ms)
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".jpg.tmp")
        with open(tmp, "wb") as fh:
            fh.write(jpeg)
        os.replace(tmp, path)
        return rel
