"""The pluggable recognition-engine interface.

A ``PlateRecognizer`` turns a frame into vehicle observations (box, class,
optional plate box and optional plate read) and can read a plate crop.  The
camera pipeline owns tracking, occlusion filtering, voting, direction and
events, so an engine only has to answer "what is in this frame?".

Engines that detect and read in one step (a commercial API) fill in
``PlateObservation.read``; the others leave it ``None`` and the pipeline calls
:meth:`PlateRecognizer.read_plate` only for frames that pass its quality gate.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

import numpy as np

from ..geometry import box_area, intersection
from ..types import Box, OcrResult, PlateObservation, VehicleObservation


class RecognizerUnavailable(RuntimeError):
    """The engine cannot run here (missing model file, runtime or API key)."""


class PlateRecognizer(ABC):
    name: str = "base"

    @abstractmethod
    def analyze(self, frame: np.ndarray, roi_mask: np.ndarray | None = None) -> list[VehicleObservation]:
        """Detect vehicles (with class) and their plates in one BGR frame."""

    @abstractmethod
    def read_plate(self, crop: np.ndarray) -> OcrResult | None:
        """Read a BGR plate crop.  Two-line plates are returned as one string, top row first."""

    def close(self) -> None:  # noqa: B027 - optional hook
        """Release models / sessions / HTTP clients."""


def associate_plates(
    vehicles: Sequence[Box], plates: Sequence[PlateObservation], min_containment: float = 0.6
) -> list[PlateObservation | None]:
    """Assign each plate to the vehicle box that contains it.

    When several vehicle boxes contain a plate (side-by-side / overlapping
    vehicles), the vehicle whose box bottom is lowest in the image - i.e. the
    one nearest to a rear-facing camera, which is the one physically in front
    - wins, and among equals the one with the most horizontally centred plate.
    Each vehicle gets at most one plate (the highest scoring).
    """
    assigned: list[PlateObservation | None] = [None] * len(vehicles)
    order = sorted(range(len(plates)), key=lambda i: plates[i].score, reverse=True)
    for pi in order:
        pb = plates[pi].box
        area = box_area(pb)
        if area <= 0:
            continue
        best_vi = None
        best_key: tuple[float, float] | None = None
        for vi, vb in enumerate(vehicles):
            if assigned[vi] is not None:
                continue
            contain = intersection(pb, vb) / area
            if contain < min_containment:
                continue
            pcx = (pb[0] + pb[2]) / 2
            vcx = (vb[0] + vb[2]) / 2
            vw = max(vb[2] - vb[0], 1.0)
            centred = 1.0 - min(1.0, abs(pcx - vcx) / (vw / 2))
            key = (contain * (0.5 + 0.5 * centred), vb[3])
            if best_key is None or key > best_key:
                best_key = key
                best_vi = vi
        if best_vi is not None:
            assigned[best_vi] = plates[pi]
    return assigned
