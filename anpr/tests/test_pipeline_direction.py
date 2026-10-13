"""Capture-line crossing, travel direction, wrong-way and per-track voting in the camera pipeline."""

from __future__ import annotations

import numpy as np
import pytest

from anpr_service.aggregator import GateAggregator, resolve_direction
from anpr_service.config import CameraConfig, GateConfig, ServiceConfig
from anpr_service.geometry import resolve_points, segment_crossing, travel_sign
from anpr_service.pipeline import CameraPipeline
from anpr_service.recognizers.base import (
    PlateRecognizer,
    associate_plates,
    plate_plausible,
)
from anpr_service.types import (
    Direction,
    GateDirection,
    OcrResult,
    PlateObservation,
    ReadStatus,
    VehicleClass,
    VehicleObservation,
)

W, H = 640, 480


class ScriptedRecognizer(PlateRecognizer):
    """Returns pre-computed observations per frame; OCR text chosen per plate position."""

    name = "scripted"

    def __init__(self, frames: list[list[VehicleObservation]], texts: dict[int, str]) -> None:
        self.frames = frames
        self.texts = texts  # vehicle x-centre bucket -> plate text
        self.i = -1

    def analyze(self, frame, roi_mask=None):  # type: ignore[no-untyped-def]
        self.i += 1
        return self.frames[self.i] if self.i < len(self.frames) else []

    def read_plate(self, crop):  # type: ignore[no-untyped-def]
        # the crop is taken from a frame whose pixel values encode the vehicle id
        vid = int(np.median(crop[..., 0]))
        text = self.texts.get(vid)
        return OcrResult(text, [0.95] * len(text), rows=2) if text else None


def _frame_with_plates(obs: list[VehicleObservation]) -> np.ndarray:
    img = np.zeros((H, W, 3), dtype=np.uint8)
    for k, o in enumerate(obs):
        if o.plate is not None:
            x1, y1, x2, y2 = (int(v) for v in o.plate.box)
            img[y1:y2, x1:x2] = 10 + k  # vehicle id encoded in the plate pixels
            # texture so the sharpness gate passes
            img[y1:y2:2, x1:x2, 1] = 255
    return img


def _vehicle(cx: float, bottom: float, with_plate: bool = True) -> VehicleObservation:
    box = (cx - 50, bottom - 200, cx + 50, bottom)
    plate = PlateObservation(box=(cx - 30, bottom - 90, cx + 30, bottom - 60), score=0.9) if with_plate else None
    return VehicleObservation(box=box, vehicle_class=VehicleClass.BIKE, score=0.9, plate=plate)


def _cfg(direction: str = "IN") -> tuple[ServiceConfig, GateConfig, CameraConfig]:
    cfg = ServiceConfig.model_validate({
        "pipeline": {"min_plate_width_px": 30, "min_sharpness": 1.0, "min_votes": 2, "post_cross_s": 0.2},
        "gates": [{
            "id": "G1", "direction": direction,
            "cameras": [{"id": "G1-L", "role": "ANPR", "capture_line": [[0, 0.5], [1, 0.5]],
                         "in_vector": [0, -1], "gate_span": [0.0, 0.65]}],
        }],
    })
    gate, cam = cfg.camera("G1-L")
    return cfg, gate, cam


def _run(frames_obs: list[list[VehicleObservation]], texts: dict[int, str], direction: str = "IN"):
    cfg, gate, cam = _cfg(direction)
    rec = ScriptedRecognizer(frames_obs, texts)
    pipe = CameraPipeline(cfg, gate, cam, rec)
    reads = []
    for i, obs in enumerate(frames_obs):
        reads += pipe.process(_frame_with_plates(obs), 1000 + 40 * i, 1000 + 40 * i)
    reads += pipe.flush()
    return reads


def test_crossing_upwards_is_in_for_rear_camera() -> None:
    frames = [[_vehicle(200, 470 - 12 * t)] for t in range(30)]  # bottom moves 470 -> 122 (crosses y=240)
    reads = _run(frames, {10: "MH43AB1234"})
    assert len(reads) == 1
    r = reads[0]
    assert r.travel_sign == 1
    assert r.status == ReadStatus.READ and r.plate == "MH43AB1234"
    assert r.plate_jpeg and r.frame_jpeg
    assert 0.1 < r.lateral < 0.3  # x=200 of 640 over span 0..0.65
    assert resolve_direction(GateDirection.IN, r.travel_sign) == (Direction.IN, False)


def test_crossing_downwards_is_out_and_wrong_way_on_in_gate() -> None:
    frames = [[_vehicle(300, 60 + 12 * t)] for t in range(35)]
    reads = _run(frames, {10: "MH12DE4521"})
    assert len(reads) == 1 and reads[0].travel_sign == -1
    cfg, gate, _ = _cfg("IN")
    agg = GateAggregator(gate, cfg.settings, anpr_camera_ids=["G1-L"])
    agg.add_read(reads[0])
    agg.end_of_stream("G1-L")
    ev = agg.poll()[0]
    assert ev.direction == Direction.OUT and ev.wrong_way is True


def test_vehicle_that_never_crosses_emits_nothing() -> None:
    frames = [[_vehicle(200, 470 - 4 * t)] for t in range(30)]  # stops short of the line
    assert _run(frames, {10: "MH43AB1234"}) == []


def test_crossing_without_readable_plate_is_unread() -> None:
    frames = [[_vehicle(200, 470 - 12 * t, with_plate=False)] for t in range(30)]
    reads = _run(frames, {})
    assert len(reads) == 1
    assert reads[0].status == ReadStatus.UNREAD and reads[0].plate is None
    assert reads[0].frame_jpeg is not None  # crossing frame kept as evidence


def test_side_by_side_pair_gets_independent_reads() -> None:
    frames = [[_vehicle(150, 470 - 12 * t), _vehicle(450, 470 - 12 * t)] for t in range(30)]
    reads = _run(frames, {10: "MH43AB1234", 11: "MH12DE4521"})
    assert sorted(r.plate for r in reads) == ["MH12DE4521", "MH43AB1234"]
    assert len({r.track_id for r in reads}) == 2


def test_occluded_frames_are_not_voted() -> None:
    """A nearer bike passes in front of B's plate for frames 5..14.  The OCR
    returns garbage on those frames; they must be skipped, not voted."""
    frames = []
    for t in range(40):
        b = _vehicle(230, 300 + 5 * t)  # B rides towards the line slowly
        obs = [b]
        if 5 <= t < 15:
            obs.append(VehicleObservation(box=(150, 170 + 5 * t, 300, 470), vehicle_class=VehicleClass.BIKE,
                                          score=0.9, plate=None))  # nearer bike, covers B's plate
        frames.append(obs)
    cfg, gate, cam = _cfg()

    class Rec(ScriptedRecognizer):
        def read_plate(self, crop):  # type: ignore[no-untyped-def]
            text = "XY00ZZ0000" if 5 <= self.i < 15 else "KA05MN2468"
            return OcrResult(text, [0.95] * 10, rows=2)

    pipe = CameraPipeline(cfg, gate, cam, Rec(frames, {}))
    for i, obs in enumerate(frames):
        pipe.process(_frame_with_plates(obs), 1000 + 40 * i, 1000 + 40 * i)
    ctx = next(c for c in pipe._tracks.values() if c.voter.n_votes)
    assert ctx.skipped_occluded == 10
    assert ctx.voter.n_votes == 30
    res = ctx.voter.result()
    assert res.best is not None and res.best.plate == "KA05MN2468"
    assert all(c.plate != "XY00ZZ0000" for c in res.candidates)


@pytest.mark.parametrize(
    "p0,p1,expected",
    [((10, 300), (10, 200), True), ((10, 200), (10, 300), True), ((10, 300), (10, 260), False),
     ((-500, 300), (-500, 200), False)],
)
def test_segment_crossing(p0, p1, expected) -> None:  # type: ignore[no-untyped-def]
    a, b = (0.0, 240.0), (640.0, 240.0)
    assert (segment_crossing(p0, p1, a, b) is not None) is expected


def test_travel_sign_and_normalised_points() -> None:
    assert travel_sign((0, -5), [0, -1]) == 1
    assert travel_sign((0, 5), [0, -1]) == -1
    assert resolve_points([[0, 0.5], [1, 0.5]], 1280, 720) == [(0.0, 360.0), (1280.0, 360.0)]
    assert resolve_points([[0, 360], [1280, 360]], 1280, 720) == [(0.0, 360.0), (1280.0, 360.0)]


def test_resolve_direction_matrix() -> None:
    assert resolve_direction(GateDirection.BOTH, -1) == (Direction.OUT, False)
    assert resolve_direction(GateDirection.OUT, -1) == (Direction.OUT, False)
    assert resolve_direction(GateDirection.OUT, 1) == (Direction.IN, True)


def test_plate_association_prefers_containing_plausible_vehicle() -> None:
    near = (100.0, 0.0, 500.0, 480.0)  # big near bike, top clipped
    far = (380.0, 40.0, 520.0, 260.0)
    far_plate = PlateObservation(box=(420.0, 150.0, 480.0, 180.0), score=0.9)
    near_plate = PlateObservation(box=(250.0, 250.0, 350.0, 300.0), score=0.9)
    # far plate lies inside the near box too, but far too high above its bottom
    assert not plate_plausible(near, far_plate.box)
    assert associate_plates([near, far], [far_plate, near_plate]) == [near_plate, far_plate]
