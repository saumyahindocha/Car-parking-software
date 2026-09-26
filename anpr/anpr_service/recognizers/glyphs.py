"""Glyph template bank and template-matching character classifier.

Templates are rendered at start-up from OpenCV's Hershey vector fonts (no
font files, no licence issues) in several weights.  Each glyph is
binarised, tightly cropped and normalised into a fixed cell preserving its
aspect ratio, so thin characters (``1``, ``I``) stay thin.  Classification is
a single matrix product of the normalised cell against all templates
(normalised cross-correlation).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import cv2
import numpy as np

ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
CELL_W = 24
CELL_H = 32

FONTS = (cv2.FONT_HERSHEY_SIMPLEX, cv2.FONT_HERSHEY_DUPLEX)
# Stroke thickness as a fraction of the rendered glyph height.
WEIGHTS = (0.07, 0.11, 0.15)


def normalize_cell(ink: np.ndarray) -> np.ndarray | None:
    """Tight-crop an ink mask (0/1 or 0/255) and fit it into a CELL_W x CELL_H cell.

    Returns a zero-mean, unit-norm float32 vector (flattened), or None if empty.
    """
    if ink.dtype != np.uint8:
        ink = ink.astype(np.uint8)
    mask = (ink > 0).astype(np.uint8) * 255
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    crop = mask[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
    h, w = crop.shape
    scale = min((CELL_H - 4) / h, (CELL_W - 4) / w)
    nh = max(1, int(round(h * scale)))
    nw = max(1, int(round(w * scale)))
    resized = cv2.resize(crop, (nw, nh), interpolation=cv2.INTER_AREA)
    cell = np.zeros((CELL_H, CELL_W), dtype=np.float32)
    y0 = (CELL_H - nh) // 2
    x0 = (CELL_W - nw) // 2
    cell[y0 : y0 + nh, x0 : x0 + nw] = resized.astype(np.float32) / 255.0
    cell = cv2.GaussianBlur(cell, (3, 3), 0.8)
    vec = cell.reshape(-1)
    vec = vec - vec.mean()
    norm = float(np.linalg.norm(vec))
    if norm < 1e-6:
        return None
    return (vec / norm).astype(np.float32)


def render_glyph(ch: str, font: int, weight: float, height_px: int = 64) -> np.ndarray:
    """Render one character as a binary ink mask (uint8 0/255)."""
    scale = cv2.getFontScaleFromHeight(font, height_px, 1)
    thickness = max(1, int(round(weight * height_px)))
    (tw, th), base = cv2.getTextSize(ch, font, scale, thickness)
    pad = thickness * 2 + 4
    canvas = np.zeros((th + base + 2 * pad, tw + 2 * pad), dtype=np.uint8)
    cv2.putText(canvas, ch, (pad, pad + th), font, scale, 255, thickness, cv2.LINE_AA)
    _, canvas = cv2.threshold(canvas, 127, 255, cv2.THRESH_BINARY)
    return canvas


@dataclass(frozen=True)
class GlyphBank:
    labels: tuple[str, ...]
    matrix: np.ndarray  # (N, CELL_W*CELL_H)
    aspects: np.ndarray  # (N,) width/height of the tight glyph


@lru_cache(maxsize=1)
def glyph_bank() -> GlyphBank:
    labels: list[str] = []
    vecs: list[np.ndarray] = []
    aspects: list[float] = []
    for ch in ALPHABET:
        for font in FONTS:
            for weight in WEIGHTS:
                g = render_glyph(ch, font, weight)
                ys, xs = np.nonzero(g)
                v = normalize_cell(g)
                if v is None:
                    continue
                labels.append(ch)
                vecs.append(v)
                aspects.append((xs.max() - xs.min() + 1) / float(ys.max() - ys.min() + 1))
    return GlyphBank(tuple(labels), np.stack(vecs), np.array(aspects, dtype=np.float32))


def classify(ink_char: np.ndarray, top_k: int = 3) -> list[tuple[str, float]]:
    """Classify one character ink mask.  Returns [(char, score 0..1), ...] best first."""
    vec = normalize_cell(ink_char)
    if vec is None:
        return []
    bank = glyph_bank()
    scores = bank.matrix @ vec
    ys, xs = np.nonzero(ink_char)
    aspect = (xs.max() - xs.min() + 1) / float(ys.max() - ys.min() + 1)
    # Penalise aspect-ratio mismatch (log ratio), which separates 1/I/L from wide glyphs.
    aspect_pen = np.abs(np.log((aspect + 0.05) / (bank.aspects + 0.05)))
    scores = scores - 0.25 * aspect_pen
    best: dict[str, float] = {}
    for label, s in zip(bank.labels, scores.tolist()):
        if s > best.get(label, -9.0):
            best[label] = s
    ranked = sorted(best.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
    return [(ch, float(max(0.0, min(1.0, s)))) for ch, s in ranked]
