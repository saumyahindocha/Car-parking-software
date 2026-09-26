"""Builders for synthetic CameraReads used by aggregator tests."""

from __future__ import annotations

import itertools
import uuid

from anpr_service.config import CameraConfig, GateConfig, Settings
from anpr_service.types import CameraRead, PlateCandidate, ReadStatus, VehicleClass

_track = itertools.count(1)


def gate(direction: str = "IN") -> GateConfig:
    return GateConfig(
        id="G1",
        name="Gate 1",
        direction=direction,
        cameras=[
            CameraConfig(id="G1-L", role="ANPR"),
            CameraConfig(id="G1-R", role="ANPR"),
            CameraConfig(id="G1-O", role="OVERVIEW"),
        ],
    )


def settings(**kw: float) -> Settings:
    return Settings(**kw)


def read(
    cam: str,
    ts: int,
    plate: str | None,
    conf: float = 0.9,
    lateral: float = 0.5,
    travel: int = 1,
    status: ReadStatus | None = None,
    vclass: VehicleClass = VehicleClass.BIKE,
) -> CameraRead:
    st = status or (ReadStatus.READ if plate else ReadStatus.UNREAD)
    return CameraRead(
        read_id=str(uuid.uuid4()),
        camera_id=cam,
        gate_id="G1",
        track_id=next(_track),
        ts_ms=ts,
        grab_wall_ms=ts,
        travel_sign=travel,
        vehicle_class=vclass,
        status=st,
        plate=plate if st == ReadStatus.READ else None,
        confidence=conf if st == ReadStatus.READ else 0.2,
        candidates=[PlateCandidate(plate, conf)] if plate else [],
        lateral=lateral,
        n_votes=5,
        plate_jpeg=f"plate-{cam}-{ts}".encode(),
        frame_jpeg=f"frame-{cam}-{ts}".encode(),
    )
