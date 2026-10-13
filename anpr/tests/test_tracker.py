from __future__ import annotations

import itertools

import numpy as np
import pytest

from anpr_service.hungarian import _hungarian_rows_le_cols, linear_assignment
from anpr_service.tracker import SortTracker, TrackerParams


def _bike(cx: float, cy: float, scale: float = 1.0) -> tuple[float, float, float, float]:
    w, h = 120 * scale, 260 * scale
    return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


def _run(tracker: SortTracker, frames: list[list[tuple[float, float, float, float]]]) -> list[dict[int, int]]:
    """Returns per frame: detection index -> track id."""
    out = []
    for dets in frames:
        ups = tracker.update(dets)
        out.append({u.det_index: u.track_id for u in ups if u.det_index is not None})
    return out


@pytest.mark.parametrize("n", [2, 3, 4])
def test_side_by_side_tracks_keep_identity(n: int) -> None:
    xs = [200 + 170 * i for i in range(n)]  # 50 px gaps between 120 px wide bikes
    frames = []
    for t in range(30):
        scale = 1.0 - 0.012 * t  # riding away: moving up and shrinking
        frames.append([_bike(x + (i - n / 2) * 2 * t * 0.1, 600 - 12 * t, scale) for i, x in enumerate(xs)])
    tracker = SortTracker(TrackerParams(max_age=8))
    assignments = _run(tracker, frames)
    ids_first = [assignments[0][i] for i in range(n)]
    assert len(set(ids_first)) == n
    for a in assignments[1:]:
        assert [a[i] for i in range(n)] == ids_first


def test_detection_order_shuffled_each_frame() -> None:
    rng = np.random.default_rng(0)
    xs = [200, 370, 540, 710]
    tracker = SortTracker()
    ref: dict[int, int] | None = None
    for t in range(25):
        dets = [_bike(x, 600 - 10 * t) for x in xs]
        order = rng.permutation(len(dets))
        ups = tracker.update([dets[i] for i in order])
        by_x = {int(round((u.box[0] + u.box[2]) / 2)): u.track_id for u in ups if u.det_index is not None}
        mapping = {x: by_x[x] for x in xs}
        if ref is None:
            ref = mapping
        assert mapping == ref


def test_occluded_track_survives_and_reappears_with_same_id() -> None:
    tracker = SortTracker(TrackerParams(max_age=10))
    ids_before = ids_after = None
    for t in range(30):
        a = _bike(300, 650 - 12 * t)
        b = _bike(480, 650 - 12 * t)
        c = _bike(660, 650 - 12 * t)
        dets = [a, c] if 10 <= t < 16 else [a, b, c]  # b hidden for 6 frames
        ups = tracker.update(dets)
        live = {u.track_id for u in ups}
        if t == 9:
            ids_before = sorted(u.track_id for u in ups if u.det_index is not None)
        if 10 <= t < 16:
            assert len(live) == 3, "occluded track must coast, not die"
            coasting = [u for u in ups if u.det_index is None]
            assert len(coasting) == 1
            # the coasting prediction keeps moving with the others
            cb = coasting[0].box
            assert abs((cb[1] + cb[3]) / 2 - (650 - 12 * t)) < 25
        if t == 20:
            ids_after = sorted(u.track_id for u in ups if u.det_index is not None)
    assert ids_before == ids_after == [1, 2, 3]


def test_track_removed_after_max_age_and_new_id_for_new_vehicle() -> None:
    tracker = SortTracker(TrackerParams(max_age=3))
    for t in range(5):
        tracker.update([_bike(300, 600 - 10 * t)])
    for _ in range(4):
        tracker.update([])
    assert not tracker.tracks
    ups = tracker.update([_bike(300, 300)])
    assert ups[0].track_id == 2


def test_partial_overlap_pair_does_not_swap() -> None:
    # Staggered pair: the nearer bike (bigger, lower) partially overlaps the farther one.
    tracker = SortTracker()
    seen = []
    for t in range(25):
        near = _bike(400 + 2 * t, 620 - 10 * t, 1.2)
        far = _bike(500 + 2 * t, 470 - 8 * t, 0.9)
        ups = tracker.update([near, far])
        seen.append({u.det_index: u.track_id for u in ups if u.det_index is not None})
    assert all(s == seen[0] for s in seen)


def _brute(cost: np.ndarray) -> float:
    n, m = cost.shape
    best = float("inf")
    if n <= m:
        for cols in itertools.permutations(range(m), n):
            best = min(best, sum(cost[i, c] for i, c in enumerate(cols)))
    else:
        for rows in itertools.permutations(range(n), m):
            best = min(best, sum(cost[r, j] for j, r in enumerate(rows)))
    return best


@pytest.mark.parametrize("shape", [(3, 3), (2, 4), (4, 2), (5, 5)])
def test_hungarian_is_optimal(shape: tuple[int, int]) -> None:
    rng = np.random.default_rng(42)
    for _ in range(10):
        cost = rng.random(shape)
        pairs = linear_assignment(cost)
        assert len(pairs) == min(shape)
        assert abs(sum(cost[r, c] for r, c in pairs) - _brute(cost)) < 1e-9
        # the pure-python fallback too
        if shape[0] <= shape[1]:
            own = _hungarian_rows_le_cols(cost)
            assert abs(sum(cost[r, c] for r, c in own) - _brute(cost)) < 1e-9
