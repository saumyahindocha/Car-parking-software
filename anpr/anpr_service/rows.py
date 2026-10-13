"""Row-layout detection for single-line vs two-line plates (horizontal projection).

Indian two-wheeler plates are frequently two-line (``MH 43`` over
``AB 1234``).  The crop is binarised so that the characters are foreground,
plate borders are suppressed, and the per-row ink profile is analysed: two
text bands separated by a low-ink gap near the middle means two rows.  Rows
are returned top to bottom and each is OCR'd separately, then concatenated.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class RowLayout:
    rows: list[tuple[int, int]]  # (y0, y1) in crop pixels, top to bottom
    ink_polarity_dark: bool  # True when characters are darker than the plate

    @property
    def n_rows(self) -> int:
        return len(self.rows)


def to_gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 3:
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return img


def binarize_ink(gray: np.ndarray, text_is_dark: bool | None = None) -> tuple[np.ndarray, bool]:
    """Otsu binarisation returning (ink mask 0/1, text_is_dark).

    On a whole plate the text occupies the minority of the area, so unless the
    polarity is given the minority class after thresholding is taken as ink.
    Tight row crops of bold fonts can be more than half ink, so row-level
    callers pass the polarity found on the whole plate.
    """
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    _, th = cv2.threshold(blur, 0, 1, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # Judge polarity on the central region (borders can be dark frames).
    h, w = th.shape
    core = th[h // 6 : h - h // 6 or h, w // 12 : w - w // 12 or w]
    if text_is_dark is None:
        bright_fraction = float(core.mean()) if core.size else float(th.mean())
        text_is_dark = bright_fraction >= 0.5
    ink = (1 - th) if text_is_dark else th
    return ink.astype(np.uint8), text_is_dark


def suppress_borders(ink: np.ndarray) -> np.ndarray:
    """Remove plate frame lines and edge clutter touching the crop border."""
    ink = ink.copy()
    h, w = ink.shape
    # Long horizontal / vertical runs near edges are borders.
    row_fill = ink.mean(axis=1)
    col_fill = ink.mean(axis=0)
    edge_rows = max(1, h // 8)
    edge_cols = max(1, w // 12)
    for y in list(range(edge_rows)) + list(range(h - edge_rows, h)):
        if row_fill[y] > 0.55:
            ink[y, :] = 0
    for x in list(range(edge_cols)) + list(range(w - edge_cols, w)):
        if col_fill[x] > 0.55:
            ink[:, x] = 0
    # Connected components that touch the border and are long and thin are frame remnants.
    n, labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    for i in range(1, n):
        x, y, cw, ch, _area = stats[i]
        touches = x == 0 or y == 0 or x + cw >= w or y + ch >= h
        if touches and (cw > 0.6 * w or ch > 0.9 * h or cw < 2 or ch < 2):
            ink[labels == i] = 0
    return ink


def detect_rows(crop: np.ndarray, max_rows: int = 2) -> RowLayout:
    """Find text rows in a plate crop using the horizontal projection profile."""
    gray = to_gray(crop)
    h, w = gray.shape[:2]
    if h < 6 or w < 6:
        return RowLayout(rows=[(0, h)], ink_polarity_dark=True)
    ink, dark = binarize_ink(gray)
    ink = suppress_borders(ink)
    profile = ink.sum(axis=1).astype(np.float64) / max(w, 1)
    if profile.max() <= 0:
        return RowLayout(rows=[(0, h)], ink_polarity_dark=dark)
    k = max(1, h // 30)
    if k > 1:
        profile = np.convolve(profile, np.ones(k) / k, mode="same")
    peak = profile.max()
    text_rows = profile > 0.12 * peak

    bands: list[list[int]] = []
    y = 0
    while y < h:
        if text_rows[y]:
            y0 = y
            while y < h and text_rows[y]:
                y += 1
            bands.append([y0, y])
        else:
            y += 1
    if not bands:
        return RowLayout(rows=[(0, h)], ink_polarity_dark=dark)
    # Merge bands separated by tiny gaps (broken strokes).
    merged: list[list[int]] = [bands[0]]
    for b in bands[1:]:
        if b[0] - merged[-1][1] <= max(1, int(0.03 * h)):
            merged[-1][1] = b[1]
        else:
            merged.append(b)
    # Keep substantial bands (by height and ink mass).
    ink_mass = [float(profile[b0:b1].sum()) for b0, b1 in merged]
    total_mass = sum(ink_mass) or 1.0
    keep = [
        b
        for b, m in zip(merged, ink_mass)
        if (b[1] - b[0]) >= 0.12 * h and m >= 0.12 * total_mass
    ]
    if not keep:
        keep = [max(merged, key=lambda b: b[1] - b[0])]

    aspect = w / float(h)
    if len(keep) >= 2 and max_rows >= 2 and aspect < 3.2:
        keep = sorted(keep, key=lambda b: b[1] - b[0], reverse=True)[:2]
        keep.sort(key=lambda b: b[0])
        (a0, a1), (b0, b1) = keep
        gap_mid = (a1 + b0) / 2.0
        if 0.25 * h <= gap_mid <= 0.75 * h:
            pad = max(1, int(0.04 * h))
            split = int(round(gap_mid))
            return RowLayout(
                rows=[(max(0, a0 - pad), min(split, a1 + pad)), (max(split, b0 - pad), min(h, b1 + pad))],
                ink_polarity_dark=dark,
            )
    y0 = min(b[0] for b in keep)
    y1 = max(b[1] for b in keep)
    pad = max(1, int(0.05 * h))
    return RowLayout(rows=[(max(0, y0 - pad), min(h, y1 + pad))], ink_polarity_dark=dark)


def split_rows(crop: np.ndarray, max_rows: int = 2) -> list[np.ndarray]:
    """Return the row images of a plate crop, top to bottom."""
    layout = detect_rows(crop, max_rows=max_rows)
    return [crop[y0:y1] for y0, y1 in layout.rows if y1 > y0]
