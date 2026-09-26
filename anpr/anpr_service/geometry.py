"""Small geometry helpers: boxes, IoU, ROI polygons and capture-line crossing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from .types import Box

Point = tuple[float, float]


def resolve_points(points: Sequence[Sequence[float]] | None, width: int, height: int) -> list[Point] | None:
    """Convert config points to pixels.

    Coordinates where *every* value is within ``[0, 1]`` are treated as
    fractions of the frame size, which keeps configs resolution independent
    (the same YAML works for the 2560x1440 camera and a downscaled replay).
    """
    if not points:
        return None
    pts = [(float(p[0]), float(p[1])) for p in points]
    if all(0.0 <= x <= 1.0 and 0.0 <= y <= 1.0 for x, y in pts):
        return [(x * width, y * height) for x, y in pts]
    return pts


def box_area(b: Box) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def intersection(a: Box, b: Box) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return max(0.0, w) * max(0.0, h)


def iou(a: Box, b: Box) -> float:
    inter = intersection(a, b)
    union = box_area(a) + box_area(b) - inter
    return inter / union if union > 0 else 0.0


def covered_fraction(a: Box, b: Box) -> float:
    """Fraction of ``a``'s area covered by ``b``."""
    area = box_area(a)
    return intersection(a, b) / area if area > 0 else 0.0


def iou_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Pairwise IoU of ``a`` (N,4) against ``b`` (M,4)."""
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)), dtype=np.float64)
    a = a[:, None, :]
    b = b[None, :, :]
    iw = np.clip(np.minimum(a[..., 2], b[..., 2]) - np.maximum(a[..., 0], b[..., 0]), 0, None)
    ih = np.clip(np.minimum(a[..., 3], b[..., 3]) - np.maximum(a[..., 1], b[..., 1]), 0, None)
    inter = iw * ih
    area_a = (a[..., 2] - a[..., 0]) * (a[..., 3] - a[..., 1])
    area_b = (b[..., 2] - b[..., 0]) * (b[..., 3] - b[..., 1])
    union = area_a + area_b - inter
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(union > 0, inter / union, 0.0)
    return out


def box_center(b: Box) -> Point:
    return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)


def box_anchor(b: Box, anchor: str = "bottom") -> Point:
    """Tracking reference point: bottom-centre (ground contact) or centre."""
    if anchor == "center":
        return box_center(b)
    return ((b[0] + b[2]) / 2.0, b[3])


def expand_box(b: Box, frac: float, width: int, height: int) -> tuple[int, int, int, int]:
    w = b[2] - b[0]
    h = b[3] - b[1]
    x1 = int(max(0, np.floor(b[0] - w * frac)))
    y1 = int(max(0, np.floor(b[1] - h * frac)))
    x2 = int(min(width, np.ceil(b[2] + w * frac)))
    y2 = int(min(height, np.ceil(b[3] + h * frac)))
    return x1, y1, x2, y2


def point_in_polygon(p: Point, polygon: Sequence[Point] | None) -> bool:
    if not polygon:
        return True
    x, y = p
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def side_of_line(p: Point, a: Point, b: Point) -> float:
    """Signed area: >0 on one side of line a->b, <0 on the other, 0 on it."""
    return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])


@dataclass(frozen=True)
class Crossing:
    point: Point  # intersection with the capture line (pixels)
    t: float  # position along the capture line, 0 at a, 1 at b


def segment_crossing(p0: Point, p1: Point, a: Point, b: Point, margin: float = 0.1) -> Crossing | None:
    """Did the motion p0 -> p1 cross capture line a-b?

    Returns the intersection when the two points are on strictly different
    sides (or p1 lies exactly on it) and the intersection lies within the line
    segment extended by ``margin`` of its length at both ends.
    """
    s0 = side_of_line(p0, a, b)
    s1 = side_of_line(p1, a, b)
    if s0 == 0 or (s0 > 0) == (s1 > 0) and s1 != 0:
        return None
    denom = s0 - s1
    if denom == 0:
        return None
    k = s0 / denom
    ix = p0[0] + (p1[0] - p0[0]) * k
    iy = p0[1] + (p1[1] - p0[1]) * k
    dx, dy = b[0] - a[0], b[1] - a[1]
    seg_len2 = dx * dx + dy * dy
    if seg_len2 == 0:
        return None
    t = ((ix - a[0]) * dx + (iy - a[1]) * dy) / seg_len2
    if t < -margin or t > 1 + margin:
        return None
    return Crossing(point=(ix, iy), t=t)


def travel_sign(motion: Point, in_vector: Sequence[float]) -> int:
    """+1 when ``motion`` points along ``in_vector`` (entering), -1 otherwise."""
    dot = motion[0] * float(in_vector[0]) + motion[1] * float(in_vector[1])
    return 1 if dot >= 0 else -1
