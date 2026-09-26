"""Pure-numpy helpers for YOLO-style ONNX detectors: letterbox, output decode and NMS.

Supported raw output layouts (batch 1):

* ``yolov8`` / YOLO11 / RT-DETR-like exports: ``(1, 4 + nc, N)`` - rows are
  ``cx, cy, w, h, class scores...`` (no objectness), transposed.
* ``yolov5`` / YOLOX (``decode_in_inference=True``): ``(1, N, 5 + nc)`` -
  ``cx, cy, w, h, objectness, class scores...``.

``auto`` picks ``yolov8`` when the second axis is the short one.
"""

from __future__ import annotations

import cv2
import numpy as np


def letterbox(img: np.ndarray, size: int, color: int = 114) -> tuple[np.ndarray, float, tuple[float, float]]:
    """Resize keeping aspect into a ``size`` x ``size`` canvas.  Returns (img, scale, (pad_x, pad_y))."""
    h, w = img.shape[:2]
    scale = min(size / h, size / w)
    nh, nw = int(round(h * scale)), int(round(w * scale))
    resized = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((size, size, 3), color, dtype=np.uint8)
    px, py = (size - nw) / 2.0, (size - nh) / 2.0
    x0, y0 = int(round(px - 0.1)), int(round(py - 0.1))
    canvas[y0 : y0 + nh, x0 : x0 + nw] = resized
    return canvas, scale, (float(x0), float(y0))


def to_nchw(img_bgr: np.ndarray) -> np.ndarray:
    rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    return np.ascontiguousarray(rgb.transpose(2, 0, 1)[None].astype(np.float32) / 255.0)


def decode(output: np.ndarray, conf_threshold: float, fmt: str = "auto") -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Decode a raw detector output into (boxes xyxy, scores, class_ids) in input-image pixels."""
    out = np.asarray(output, dtype=np.float32)
    if out.ndim == 3:
        out = out[0]
    if out.ndim != 2:
        raise ValueError(f"unexpected detector output shape {output.shape}")
    if fmt == "auto":
        fmt = "yolov8" if out.shape[0] < out.shape[1] else "yolov5"
    if fmt == "yolov8":
        if out.shape[0] < out.shape[1]:
            out = out.T
        xywh = out[:, :4]
        cls_scores = out[:, 4:]
        if cls_scores.shape[1] == 0:
            raise ValueError("detector output has no class columns")
        class_ids = cls_scores.argmax(axis=1)
        scores = cls_scores[np.arange(len(out)), class_ids]
    elif fmt == "yolov5":
        xywh = out[:, :4]
        obj = out[:, 4]
        cls_scores = out[:, 5:]
        if cls_scores.shape[1] == 0:
            class_ids = np.zeros(len(out), dtype=np.int64)
            scores = obj
        else:
            class_ids = cls_scores.argmax(axis=1)
            scores = obj * cls_scores[np.arange(len(out)), class_ids]
    else:
        raise ValueError(f"unknown detector format {fmt!r}")
    keep = scores >= conf_threshold
    xywh, scores, class_ids = xywh[keep], scores[keep], class_ids[keep]
    boxes = np.empty_like(xywh)
    boxes[:, 0] = xywh[:, 0] - xywh[:, 2] / 2
    boxes[:, 1] = xywh[:, 1] - xywh[:, 3] / 2
    boxes[:, 2] = xywh[:, 0] + xywh[:, 2] / 2
    boxes[:, 3] = xywh[:, 1] + xywh[:, 3] / 2
    return boxes, scores.astype(np.float32), class_ids.astype(np.int64)


def nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> np.ndarray:
    """Greedy non-maximum suppression.  Returns kept indices, best first."""
    if len(boxes) == 0:
        return np.zeros((0,), dtype=np.int64)
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = np.maximum(0, x2 - x1) * np.maximum(0, y2 - y1)
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size:
        i = int(order[0])
        keep.append(i)
        rest = order[1:]
        xx1 = np.maximum(x1[i], x1[rest])
        yy1 = np.maximum(y1[i], y1[rest])
        xx2 = np.minimum(x2[i], x2[rest])
        yy2 = np.minimum(y2[i], y2[rest])
        inter = np.maximum(0, xx2 - xx1) * np.maximum(0, yy2 - yy1)
        iou = inter / np.maximum(areas[i] + areas[rest] - inter, 1e-9)
        order = rest[iou <= iou_threshold]
    return np.array(keep, dtype=np.int64)


def batched_nms(boxes: np.ndarray, scores: np.ndarray, class_ids: np.ndarray, iou_threshold: float) -> np.ndarray:
    """Class-aware NMS (boxes of different classes never suppress each other)."""
    if len(boxes) == 0:
        return np.zeros((0,), dtype=np.int64)
    offset = (boxes.max() + 1.0) * class_ids.astype(np.float32)[:, None]
    return nms(boxes + offset, scores, iou_threshold)


def unletterbox(boxes: np.ndarray, scale: float, pad: tuple[float, float], width: int, height: int) -> np.ndarray:
    out = boxes.copy()
    out[:, [0, 2]] = (out[:, [0, 2]] - pad[0]) / scale
    out[:, [1, 3]] = (out[:, [1, 3]] - pad[1]) / scale
    out[:, [0, 2]] = out[:, [0, 2]].clip(0, width)
    out[:, [1, 3]] = out[:, [1, 3]].clip(0, height)
    return out
