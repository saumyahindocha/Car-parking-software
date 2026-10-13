from __future__ import annotations

from helpers import gate, read, settings

from anpr_service.aggregator import GateAggregator
from anpr_service.types import GateDirection, OverviewSnapshot, ReadStatus

T0 = 1_700_000_000_000


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def make(direction: str = "IN", **kw: float) -> tuple[GateAggregator, Clock]:
    clock = Clock()
    agg = GateAggregator(gate(direction), settings(**kw), anpr_camera_ids=["G1-L", "G1-R"], clock=clock)
    return agg, clock


def advance(agg: GateAggregator, ts: int) -> list:
    agg.watermark("G1-L", ts)
    agg.watermark("G1-R", ts)
    return agg.poll()


def test_cross_camera_merge_keeps_best_read_and_all_images() -> None:
    agg, _ = make()
    agg.add_read(read("G1-L", T0, "MH14GX0786", conf=0.80, lateral=0.50))
    agg.add_read(read("G1-R", T0 + 40, "MH14GX0786", conf=0.95, lateral=0.52))
    events = agg.poll()  # both cameras contributed -> emitted without waiting
    assert len(events) == 1
    ev = events[0]
    assert ev.camera_ids == ["G1-L", "G1-R"]
    assert ev.plate == "MH14GX0786"
    assert ev.confidence == 0.95
    assert ev.primary.camera_id == "G1-R"
    assert [o.camera_id for o in ev.others] == ["G1-L"]
    assert ev.ts_ms == T0  # earliest crossing
    assert agg.stats.merged_cross_camera == 1


def test_confusion_aware_merge() -> None:
    agg, _ = make()
    agg.add_read(read("G1-L", T0, "MH12AB1234", conf=0.9))
    agg.add_read(read("G1-R", T0 + 3000, "MHI2A81234", conf=0.7))  # same plate, OCR confusions, 3 s later
    events = advance(agg, T0 + 10_000)
    assert len(events) == 1
    assert events[0].plate == "MH12AB1234"


def test_side_by_side_different_plates_are_two_events() -> None:
    agg, _ = make()
    agg.add_read(read("G1-L", T0, "MH43AB1234", lateral=0.2))
    agg.add_read(read("G1-R", T0 + 20, "MH12DE4521", lateral=0.8))
    events = advance(agg, T0 + 1000)
    assert sorted(e.plate for e in events) == ["MH12DE4521", "MH43AB1234"]
    assert all(len(e.camera_ids) == 1 for e in events)


def test_hold_waits_for_other_camera_watermark() -> None:
    agg, _ = make(merge_hold_s=0.5)
    agg.add_read(read("G1-L", T0, "MH43AB1234", lateral=0.3))
    agg.watermark("G1-L", T0 + 900)
    agg.watermark("G1-R", T0 + 200)  # right camera lags behind
    assert agg.poll() == []
    agg.watermark("G1-R", T0 + 499)
    assert agg.poll() == []
    agg.watermark("G1-R", T0 + 500)
    assert [e.plate for e in agg.poll()] == ["MH43AB1234"]


def test_stale_camera_does_not_block_emission() -> None:
    agg, clock = make(merge_hold_s=0.5, camera_stale_s=3.0)
    agg.watermark("G1-R", T0 - 100)
    agg.add_read(read("G1-L", T0, "MH43AB1234"))
    agg.watermark("G1-L", T0 + 1000)
    assert agg.poll() == []
    clock.t += 5.0  # G1-R silent for 5 s (stream down)
    agg.watermark("G1-L", T0 + 1100)
    assert len(agg.poll()) == 1


def test_end_of_stream_releases_hold() -> None:
    agg, _ = make()
    agg.add_read(read("G1-L", T0, "MH43AB1234"))
    agg.watermark("G1-L", T0 + 2000)
    agg.watermark("G1-R", T0)
    assert agg.poll() == []
    agg.end_of_stream("G1-R")
    assert len(agg.poll()) == 1


def test_late_read_within_merge_window_is_folded_in() -> None:
    agg, _ = make(merge_window_s=10)
    agg.add_read(read("G1-L", T0, "MH43AB1234"))
    assert len(advance(agg, T0 + 1000)) == 1
    agg.add_read(read("G1-R", T0 + 4000, "MH43A81234"))
    assert advance(agg, T0 + 6000) == []
    assert agg.stats.suppressed_late == 1


def test_dedupe_same_plate_within_60s_dropped_then_allowed() -> None:
    agg, _ = make(merge_window_s=10, dedupe_window_s=60)
    agg.add_read(read("G1-L", T0, "MH43AB1234"))
    assert len(advance(agg, T0 + 1000)) == 1
    agg.add_read(read("G1-L", T0 + 30_000, "MH43AB1234"))  # e.g. circled back through the gate
    assert advance(agg, T0 + 31_000) == []
    assert agg.stats.deduped == 1
    agg.add_read(read("G1-L", T0 + 61_000, "MH43AB1234"))
    events = advance(agg, T0 + 62_000)
    assert [e.plate for e in events] == ["MH43AB1234"]


def test_unread_merges_with_read_from_other_camera() -> None:
    agg, _ = make()
    agg.add_read(read("G1-L", T0, None, lateral=0.62))  # partial view at the left camera's edge
    agg.add_read(read("G1-R", T0 + 120, "GJ01KP7730", lateral=0.66, conf=0.93))
    events = advance(agg, T0 + 1000)
    assert len(events) == 1
    ev = events[0]
    assert ev.status == ReadStatus.READ
    assert ev.plate == "GJ01KP7730"
    assert ev.camera_ids == ["G1-L", "G1-R"]
    assert ev.primary.camera_id == "G1-R"
    assert ev.others[0].frame_jpeg is not None  # the UNREAD camera's images are kept


def test_unread_picks_closest_vehicle_when_side_by_side() -> None:
    agg, _ = make()
    agg.add_read(read("G1-R", T0, "MH12DE4521", lateral=0.80))
    agg.add_read(read("G1-R", T0 + 10, "MH14GX0786", lateral=0.50))
    agg.add_read(read("G1-L", T0 + 30, None, lateral=0.47))
    events = advance(agg, T0 + 1000)
    by_plate = {e.plate: e for e in events}
    assert set(by_plate) == {"MH12DE4521", "MH14GX0786"}
    assert by_plate["MH14GX0786"].camera_ids == ["G1-L", "G1-R"]
    assert by_plate["MH12DE4521"].camera_ids == ["G1-R"]


def test_unread_without_partner_is_emitted_with_images() -> None:
    agg, _ = make()
    agg.add_read(read("G1-R", T0, None, lateral=0.78))
    agg.add_read(read("G1-R", T0 + 50, "MH43AB1234", lateral=0.2))  # other vehicle, far away laterally
    events = advance(agg, T0 + 1000)
    assert sorted((e.status.value, e.plate or "") for e in events) == [("READ", "MH43AB1234"), ("UNREAD", "")]
    unread = next(e for e in events if e.status == ReadStatus.UNREAD)
    assert unread.primary.frame_jpeg and unread.primary.plate_jpeg


def test_two_unreads_from_same_camera_stay_separate() -> None:
    agg, _ = make()
    agg.add_read(read("G1-L", T0, None, lateral=0.30))
    agg.add_read(read("G1-L", T0 + 20, None, lateral=0.35))
    assert len(advance(agg, T0 + 1000)) == 2


def test_near_identical_reads_of_same_crossing_merge() -> None:
    agg, _ = make()
    agg.add_read(read("G1-L", T0, "MH14GX0786", conf=0.9, lateral=0.5))
    agg.add_read(read("G1-R", T0 + 30, "MH14OX0786", conf=0.7, lateral=0.52))  # one OCR error
    events = advance(agg, T0 + 1000)
    assert len(events) == 1 and events[0].plate == "MH14GX0786"


def test_direction_and_wrong_way() -> None:
    agg, _ = make("IN")
    agg.add_read(read("G1-L", T0, "MH43AB1234", travel=1, lateral=0.2))
    agg.add_read(read("G1-R", T0, "MH12DE4521", travel=-1, lateral=0.8))
    events = {e.plate: e for e in advance(agg, T0 + 1000)}
    assert events["MH43AB1234"].direction.value == "IN" and not events["MH43AB1234"].wrong_way
    assert events["MH12DE4521"].direction.value == "OUT" and events["MH12DE4521"].wrong_way


def test_direction_follows_config_update() -> None:
    agg, _ = make("IN")
    agg.update_config(gate("OUT"), agg.settings)
    assert agg.gate.direction == GateDirection.OUT
    agg.add_read(read("G1-L", T0, "MH43AB1234", travel=-1))
    ev = advance(agg, T0 + 1000)[0]
    assert ev.direction.value == "OUT" and not ev.wrong_way
    agg.update_config(gate("BOTH"), agg.settings)
    agg.add_read(read("G1-L", T0 + 70_000, "MH12DE4521", travel=1))
    ev = advance(agg, T0 + 71_000)[0]
    assert ev.direction.value == "IN" and not ev.wrong_way


def test_overview_snapshot_attached() -> None:
    agg, _ = make()
    for k in range(10):
        agg.add_overview(OverviewSnapshot("G1-O", "G1", T0 - 1000 + 250 * k, f"ov{k}".encode()))
    agg.add_read(read("G1-L", T0 + 10, "MH43AB1234"))
    ev = advance(agg, T0 + 1000)[0]
    assert ev.overview_jpeg == b"ov4"  # T0 - 1000 + 1000 = closest snapshot to the crossing


def test_restarted_camera_rejoins_after_end_of_stream() -> None:
    agg, _ = make()
    agg.end_of_stream("G1-R")  # worker crashed
    agg.watermark("G1-R", T0 - 100)  # restarted worker streams again
    agg.add_read(read("G1-L", T0, "MH43AB1234"))
    agg.watermark("G1-L", T0 + 2000)
    assert agg.poll() == []  # waits for the restarted camera again
    agg.watermark("G1-R", T0 + 600)
    assert len(agg.poll()) == 1
