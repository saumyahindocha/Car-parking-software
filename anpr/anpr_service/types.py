"""Shared value types passed between pipeline stages and processes.

Everything that crosses a process boundary (``CameraRead``, ``Watermark``...)
is a plain dataclass holding only picklable primitives and ``bytes`` (JPEGs),
never numpy frames, so the multiprocessing queues stay cheap.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

Box = tuple[float, float, float, float]  # x1, y1, x2, y2 in pixels


class VehicleClass(str, Enum):
    BIKE = "BIKE"
    CAR = "CAR"
    OTHER = "OTHER"


class Direction(str, Enum):
    IN = "IN"
    OUT = "OUT"


class GateDirection(str, Enum):
    IN = "IN"
    OUT = "OUT"
    BOTH = "BOTH"


class CameraRole(str, Enum):
    ANPR = "ANPR"
    OVERVIEW = "OVERVIEW"


class CameraSide(str, Enum):
    LEFT = "LEFT"
    RIGHT = "RIGHT"
    CENTER = "CENTER"
    OVERVIEW = "OVERVIEW"


class ReadStatus(str, Enum):
    READ = "READ"
    UNREAD = "UNREAD"


@dataclass
class OcrResult:
    """Result of reading one plate crop (all rows concatenated top to bottom)."""

    text: str
    char_confs: list[float]
    rows: int = 1
    # Optional per-position alternatives [(char, score), ...] best first.
    alternatives: list[list[tuple[str, float]]] | None = None

    @property
    def confidence(self) -> float:
        if not self.char_confs:
            return 0.0
        return float(sum(self.char_confs) / len(self.char_confs))


@dataclass
class PlateObservation:
    box: Box
    score: float
    # Recognisers that read the plate as part of detection (e.g. a commercial
    # API) fill this in; otherwise the pipeline calls ``read_plate`` on the crop.
    read: OcrResult | None = None


@dataclass
class VehicleObservation:
    box: Box
    vehicle_class: VehicleClass
    score: float
    plate: PlateObservation | None = None


@dataclass
class PlateCandidate:
    plate: str
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {"plate": self.plate, "confidence": round(float(self.confidence), 4)}


@dataclass
class CameraRead:
    """One vehicle crossing the capture line as seen by one ANPR camera."""

    read_id: str
    camera_id: str
    gate_id: str
    track_id: int
    ts_ms: int  # epoch ms of the frame in which the crossing was observed
    grab_wall_ms: int  # wall-clock ms at which that frame was grabbed (latency base)
    travel_sign: int  # +1 travelling along the camera's in_vector, -1 against it
    vehicle_class: VehicleClass
    status: ReadStatus
    plate: str | None
    confidence: float
    candidates: list[PlateCandidate]
    lateral: float  # position across the gate at the crossing, 0 (left) .. 1 (right)
    n_votes: int
    plate_jpeg: bytes | None = None
    frame_jpeg: bytes | None = None
    timings_ms: dict[str, float] = field(default_factory=dict)


@dataclass
class Watermark:
    """Camera has processed every frame up to ``ts_ms`` (event time)."""

    camera_id: str
    ts_ms: int


@dataclass
class OverviewSnapshot:
    camera_id: str
    gate_id: str
    ts_ms: int
    jpeg: bytes


@dataclass
class EndOfStream:
    camera_id: str


@dataclass
class ConfigUpdate:
    """Sent by the supervisor to a gate aggregator after a config refresh."""

    gate: dict[str, Any]
    settings: dict[str, Any]
