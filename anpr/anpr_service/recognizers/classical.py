"""Pure-OpenCV fallback recogniser (no trained models).

* Vehicles: MOG2 background subtraction on a downscaled frame, morphology and
  blob extraction.  Blobs of side-by-side vehicles that touch are split using
  the plates found inside them (plate-guided splitting).
* Plates: bright (white / yellow), rectangular, text-bearing regions inside a
  vehicle blob, rejected when truncated by the frame border.
* OCR: row-layout detection (single / two-line), connected-component
  character segmentation and template matching against Hershey-font glyphs.

It is accurate on the synthetic replay videos produced by
``python -m anpr_service synth`` so the whole system can be demonstrated end
to end without trained models.  On real camera footage it is only a
diagnostic fallback: use ``LocalOnnxRecognizer`` or ``CommercialApiRecognizer``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import cv2
import numpy as np

from ..config import ClassicalConfig
from ..rows import binarize_ink, detect_rows, suppress_borders, to_gray
from ..types import Box, OcrResult, PlateObservation, VehicleClass, VehicleObservation
from .base import PlateRecognizer, associate_plates, plate_plausible
from .glyphs import classify

log = logging.getLogger(__name__)


@dataclass
class _Glyph:
    """Horizontal extent of one character (possibly several connected components)."""

    x0: int
    x1: int
    labels: list[int]


class ClassicalRecognizer(PlateRecognizer):
    name = "classical"

    def __init__(self, cfg: ClassicalConfig | None = None, min_plate_width_px: int = 36) -> None:
        self.cfg = cfg or ClassicalConfig()
        self.min_plate_width_px = min_plate_width_px
        self._bg: cv2.BackgroundSubtractor | None = None
        self._frames = 0
        self._roi_small: np.ndarray | None = None

    # ------------------------------------------------------------------ vehicles
    def analyze(self, frame: np.ndarray, roi_mask: np.ndarray | None = None) -> list[VehicleObservation]:
        cfg = self.cfg
        h, w = frame.shape[:2]
        scale = min(1.0, cfg.bg_max_width / float(w))
        small = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1.0 else frame
        if self._bg is None:
            bg = cv2.createBackgroundSubtractorMOG2(
                history=cfg.bg_history, varThreshold=cfg.bg_var_threshold, detectShadows=False
            )
            # Start from a tight noise model so dark vehicles on dark asphalt are
            # foreground from the first frames (the default initial variance is huge).
            bg.setVarInit(cfg.bg_var_init)
            bg.setVarMin(4.0)
            self._bg = bg
        fg = self._bg.apply(small)
        self._frames += 1
        if self._frames <= cfg.warmup_frames:
            return []
        _, fg = cv2.threshold(fg, 127, 255, cv2.THRESH_BINARY)
        if roi_mask is not None:
            if self._roi_small is None or self._roi_small.shape != fg.shape:
                self._roi_small = cv2.resize(roi_mask, (fg.shape[1], fg.shape[0]), interpolation=cv2.INTER_NEAREST)
            fg = cv2.bitwise_and(fg, self._roi_small)
        k_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        close_sz = max(3, int(round(fg.shape[1] / 90)) | 1)
        k_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_sz, close_sz))
        fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, k_open)
        fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, k_close)

        contours, _ = cv2.findContours(fg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        inv = 1.0 / scale
        out: list[VehicleObservation] = []
        for bx, by, bw, bh in self._blobs(contours, fg.shape):
            blob_small = (bx, by, bx + bw, by + bh)
            blob = (bx * inv, by * inv, min(w, (bx + bw) * inv), min(h, (by + bh) * inv))
            plates = self.find_plates(frame, blob)
            own = [p for p in plates if plate_plausible(blob, p.box)]
            # Plates too high for this blob belong to farther vehicles merged into it.
            for p in plates:
                if p not in own:
                    out.append(self._vehicle_from_plate(p, (w, h)))
            if len(own) >= 2:
                out.extend(self._split_blob(fg, blob_small, inv, own, (w, h)))
            else:
                out.append(
                    VehicleObservation(
                        box=blob,
                        vehicle_class=self._classify(blob, (w, h)),
                        score=0.5,
                        plate=own[0] if own else None,
                    )
                )
        return out

    @staticmethod
    def _vehicle_from_plate(plate: PlateObservation, size: tuple[int, int]) -> VehicleObservation:
        """Approximate box of a two-wheeler from its rear plate (used when it is
        merged into a nearer vehicle's blob)."""
        x1, y1, x2, y2 = plate.box
        pw = x2 - x1
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        bottom = min(float(size[1]), cy + 1.5 * pw)
        box = (max(0.0, cx - 1.2 * pw), max(0.0, bottom - 5.0 * pw), min(float(size[0]), cx + 1.2 * pw), bottom)
        return VehicleObservation(box=box, vehicle_class=VehicleClass.BIKE, score=0.3, plate=plate)

    def _blobs(self, contours: list[np.ndarray], shape: tuple[int, ...]) -> list[tuple[int, int, int, int]]:
        """Foreground blobs as (x, y, w, h), with fragments of one vehicle re-joined.

        A vehicle often splits into stacked parts (e.g. a dark wheel over a
        dark road marking separates from the body).  Blobs that overlap
        horizontally and are vertically adjacent are merged; side-by-side
        vehicles are horizontally disjoint and stay separate.
        """
        fh, fw = shape[:2]
        boxes = [list(cv2.boundingRect(c)) for c in contours if cv2.contourArea(c) >= 0.15 * self.cfg.min_vehicle_area_frac * fh * fw]
        gap = max(2, int(0.03 * fh))
        merged = True
        while merged and len(boxes) > 1:
            merged = False
            for i in range(len(boxes)):
                for j in range(i + 1, len(boxes)):
                    a, b = boxes[i], boxes[j]
                    ox = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
                    vy = max(a[1], b[1]) - min(a[1] + a[3], b[1] + b[3])  # vertical gap (<0 = overlap)
                    narrow = min(a[2], b[2])
                    if ox >= 0.6 * narrow and vy <= gap:
                        x0, y0 = min(a[0], b[0]), min(a[1], b[1])
                        x1, y1 = max(a[0] + a[2], b[0] + b[2]), max(a[1] + a[3], b[1] + b[3])
                        boxes[i] = [x0, y0, x1 - x0, y1 - y0]
                        del boxes[j]
                        merged = True
                        break
                if merged:
                    break
        min_area = self.cfg.min_vehicle_area_frac * fh * fw
        return [
            (x, y, w, h)
            for x, y, w, h in boxes
            if w * h >= min_area and w >= 0.012 * fw and w / float(max(h, 1)) >= 0.15
        ]

    def _split_blob(
        self,
        fg: np.ndarray,
        blob_small: tuple[int, int, int, int],
        inv: float,
        plates: list[PlateObservation],
        size: tuple[int, int],
    ) -> list[VehicleObservation]:
        """Split a merged blob into one vehicle per plate along plate-centre midpoints."""
        plates = sorted(plates, key=lambda p: (p.box[0] + p.box[2]) / 2)
        centres = [(p.box[0] + p.box[2]) / 2 / inv for p in plates]
        x0, y0, x1, y1 = blob_small
        bounds = [x0] + [int(round((a + b) / 2)) for a, b in zip(centres, centres[1:])] + [x1]
        vehicles: list[Box] = []
        for xa, xb in zip(bounds, bounds[1:]):
            part = fg[y0:y1, xa:xb]
            ys, xs = np.nonzero(part)
            if len(xs) == 0:
                vehicles.append((xa * inv, y0 * inv, xb * inv, y1 * inv))
                continue
            vehicles.append(
                (
                    (xa + xs.min()) * inv,
                    (y0 + ys.min()) * inv,
                    min(size[0], (xa + xs.max() + 1) * inv),
                    min(size[1], (y0 + ys.max() + 1) * inv),
                )
            )
        assigned = associate_plates(vehicles, plates, min_containment=0.5)
        return [
            VehicleObservation(box=vb, vehicle_class=self._classify(vb, size), score=0.5, plate=pl)
            for vb, pl in zip(vehicles, assigned)
        ]

    def _classify(self, box: Box, size: tuple[int, int]) -> VehicleClass:
        bw = box[2] - box[0]
        bh = max(box[3] - box[1], 1.0)
        if bw / bh >= self.cfg.car_min_aspect and bw >= 0.3 * size[0]:
            return VehicleClass.CAR
        if bw / bh <= 1.1:
            return VehicleClass.BIKE
        return VehicleClass.OTHER

    # -------------------------------------------------------------------- plates
    def find_plates(self, frame: np.ndarray, box: Box) -> list[PlateObservation]:
        cfg = self.cfg
        fh, fw = frame.shape[:2]
        x1, y1 = max(0, int(box[0])), max(0, int(box[1]))
        x2, y2 = min(fw, int(np.ceil(box[2]))), min(fh, int(np.ceil(box[3])))
        if x2 - x1 < 8 or y2 - y1 < 8:
            return []
        region = frame[y1:y2, x1:x2]
        hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
        hch, sch, vch = hsv[..., 0], hsv[..., 1], hsv[..., 2]
        white = (vch >= cfg.plate_min_v) & (sch <= cfg.plate_max_s)
        yellow = (hch >= 22) & (hch <= 34) & (sch >= 100) & (vch >= 150)  # HSRP/commercial yellow, not orange
        mask = ((white | yellow).astype(np.uint8)) * 255
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
        found: list[PlateObservation] = []
        min_w = max(12, int(self.min_plate_width_px * 0.5))
        for c in contours:
            bx, by, bw, bh = cv2.boundingRect(c)
            if bw < min_w or bh < 8:
                continue
            aspect = bw / float(bh)
            if not (cfg.plate_min_aspect <= aspect <= cfg.plate_max_aspect):
                continue
            fill = cv2.contourArea(c) / float(bw * bh)
            if fill < 0.7:
                continue
            gx1, gy1, gx2, gy2 = bx + x1, by + y1, bx + bw + x1, by + bh + y1
            if gx1 <= 1 or gy1 <= 1 or gx2 >= fw - 1 or gy2 >= fh - 1:
                continue  # truncated by the frame edge: unreadable by construction
            inner = gray[by : by + bh, bx : bx + bw]
            ink, _dark = binarize_ink(inner)
            ink = suppress_borders(ink)
            frac = float(ink.mean())
            if not (0.04 <= frac <= 0.6):
                continue
            n, _l, stats, _c = cv2.connectedComponentsWithStats(ink, connectivity=8)
            glyphish = sum(1 for i in range(1, n) if stats[i, 3] >= 0.2 * bh and stats[i, 4] >= 6)
            if glyphish < 3:
                continue
            score = float(fill) * min(1.0, glyphish / 6.0)
            found.append(PlateObservation(box=(float(gx1), float(gy1), float(gx2), float(gy2)), score=score))
        return found

    # ----------------------------------------------------------------------- OCR
    def read_plate(self, crop: np.ndarray) -> OcrResult | None:
        if crop is None or crop.size == 0 or min(crop.shape[:2]) < 8:
            return None
        layout = detect_rows(crop)
        chars: list[str] = []
        confs: list[float] = []
        alts: list[list[tuple[str, float]]] = []
        for y0, y1 in layout.rows:
            row = crop[y0:y1]
            for glyph in self._segment_row(row, layout.ink_polarity_dark):
                ranked = classify(glyph)
                if not ranked:
                    continue
                s1 = ranked[0][1]
                s2 = ranked[1][1] if len(ranked) > 1 else 0.0
                conf = float(np.clip((s1 - 0.3) / 0.45, 0.0, 1.0) * np.clip(0.55 + 4.0 * (s1 - s2), 0.0, 1.0))
                chars.append(ranked[0][0])
                confs.append(conf)
                alts.append(ranked)
        if not chars:
            return None
        return OcrResult(text="".join(chars), char_confs=confs, rows=layout.n_rows, alternatives=alts)

    def _segment_row(self, row: np.ndarray, text_is_dark: bool | None = None) -> list[np.ndarray]:
        gray = to_gray(row)
        h, w = gray.shape[:2]
        if h < 4 or w < 4:
            return []
        target_h = self.cfg.ocr_row_height
        new_w = max(8, int(round(w * target_h / float(h))))
        gray = cv2.resize(gray, (new_w, target_h), interpolation=cv2.INTER_CUBIC)
        ink, _dark = binarize_ink(gray, text_is_dark)
        ink = suppress_borders(ink)
        n, labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
        comps: list[list[int]] = []
        for i in range(1, n):
            x, y, cw, ch, area = (int(v) for v in stats[i])
            if ch < 0.35 * target_h or area < 0.01 * target_h * target_h:
                continue
            comps.append([x, y, cw, ch, i])
        if not comps:
            return []
        tallest = max(c[3] for c in comps)
        comps = [c for c in comps if c[3] >= 0.6 * tallest]
        comps.sort(key=lambda c: c[0])
        # Merge fragments that overlap horizontally (broken strokes).
        groups: list[_Glyph] = []
        for x, _y, cw, _ch, label in comps:
            if groups:
                g = groups[-1]
                overlap = min(g.x1, x + cw) - max(g.x0, x)
                if overlap > 0.5 * min(cw, g.x1 - g.x0):
                    g.x0, g.x1 = min(g.x0, x), max(g.x1, x + cw)
                    g.labels.append(label)
                    continue
            groups.append(_Glyph(x, x + cw, [label]))
        normal = [g.x1 - g.x0 for g in groups if g.x1 - g.x0 <= tallest]
        char_w = float(np.median(normal)) if normal else 0.65 * tallest
        glyphs: list[np.ndarray] = []
        for g in groups:
            x0, x1 = g.x0, g.x1
            mask = np.isin(labels[:, x0:x1], g.labels).astype(np.uint8)
            ys = np.nonzero(mask.any(axis=1))[0]
            if len(ys) == 0:
                continue
            ch_h = ys.max() - ys.min() + 1
            cw = x1 - x0
            if cw > 1.05 * ch_h and cw > 1.5 * char_w:  # touching characters
                k = max(2, int(round(cw / max(char_w, 1.0))))
                edges = self._cut_columns(mask, k)
                for a, b in zip(edges, edges[1:]):
                    part = mask[:, a:b]
                    if part.any():
                        glyphs.append(part)
            else:
                glyphs.append(mask)
        return glyphs

    @staticmethod
    def _cut_columns(mask: np.ndarray, k: int) -> list[int]:
        """Cut positions splitting ``mask`` into ``k`` glyphs at ink-profile minima."""
        cw = mask.shape[1]
        col = mask.sum(axis=0).astype(np.float64)
        step = cw / k
        edges = [0]
        for i in range(1, k):
            centre = int(round(i * step))
            lo = max(edges[-1] + 1, int(centre - 0.3 * step))
            hi = min(cw - 1, int(centre + 0.3 * step))
            if hi <= lo:
                edges.append(centre)
                continue
            edges.append(lo + int(np.argmin(col[lo:hi + 1])))
        edges.append(cw)
        return edges
