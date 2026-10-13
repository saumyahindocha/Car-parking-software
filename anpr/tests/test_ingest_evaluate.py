from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from anpr_service.evaluate import evaluate, format_report
from anpr_service.imaging import ImageStore
from anpr_service.ingest import ReplaySource


def _video(path: Path, n: int = 10, fps: float = 10.0) -> Path:
    wr = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, (64, 48))
    for i in range(n):
        wr.write(np.full((48, 64, 3), i * 10, dtype=np.uint8))
    wr.release()
    return path


def test_replay_source_timestamps_and_eos(tmp_path: Path) -> None:
    src = ReplaySource(str(_video(tmp_path / "v.avi")), start_epoch_ms=1_000_000)
    pkts = []
    while (p := src.read()) is not None:
        pkts.append(p)
    assert src.eos
    assert [p.ts_ms for p in pkts] == [1_000_000 + 100 * i for i in range(10)]
    assert [p.index for p in pkts] == list(range(10))


def test_replay_source_loop_and_realtime(tmp_path: Path) -> None:
    src = ReplaySource(str(_video(tmp_path / "v.avi", n=4, fps=50)), start_epoch_ms=0, loop=True, realtime=True)
    t0 = time.monotonic()
    ts = [src.read().ts_ms for _ in range(10)]  # type: ignore[union-attr]
    assert ts == [20 * i for i in range(10)]  # monotonic across the loop
    assert time.monotonic() - t0 >= 0.15  # paced at ~50 fps
    assert not src.eos


def test_image_store_layout(tmp_path: Path) -> None:
    store = ImageStore(tmp_path)
    ts = int(time.mktime((2026, 3, 7, 9, 30, 0, 0, 0, -1)) * 1000)
    rel = store.write("G2", "abc", "plate_crop", ts, b"\xff\xd8jpeg")
    assert rel == "2026/03/07/G2/abc_plate_crop.jpg"
    assert (tmp_path / rel).read_bytes() == b"\xff\xd8jpeg"
    assert not list(tmp_path.rglob("*.tmp"))


def test_evaluate_reports_per_camera_and_side_by_side(synthetic_gate: tuple[Path, dict[str, Any]]) -> None:
    clip_dir, gt = synthetic_gate
    report = evaluate(clip_dir)
    s = report["summary"]
    assert s["G1-L"]["exact_accuracy"] == 1.0 and s["G1-R"]["exact_accuracy"] == 1.0
    assert s["G1-L"]["read_rate"] == 1.0
    assert s["GATE:G1"]["approx_match_rate"] == 1.0
    rep = report["sets"][0]
    assert rep["gate"]["extra_events"] == 0
    assert rep["gate"]["unreadable_emitted_unread"] == 1
    assert rep["cameras"]["G1-L"]["side_by_side"]["vehicles"] >= 3
    assert "side-by-side" in format_report(report)


def test_synth_generates_entry_and_exit_gates(tmp_path: Path) -> None:
    import yaml

    from anpr_service.synth import default_scenario, generate

    exit_sc = default_scenario("G2", compact=True, direction="OUT", time_offset_s=8.0)
    entry_sc = default_scenario("G1", compact=True)
    assert exit_sc.in_vector == (0.0, 1.0)
    assert [v.plate for v in exit_sc.vehicles] == [v.plate for v in entry_sc.vehicles]
    for a, b in zip(entry_sc.vehicles, exit_sc.vehicles):
        assert abs(b.cross_s - a.cross_s - 8.0) < 0.5

    cfg_path = generate(tmp_path, gates=["G1", "G2"], width=320, height=180, fps=5, compact=True,
                        overview=False, ext=".avi", wrong_way=True, exit_delay_s=6.0)
    cfg = yaml.safe_load(cfg_path.read_text())
    g1, g2 = cfg["gates"]
    assert (g1["direction"], g2["direction"]) == ("IN", "OUT")
    assert g1["cameras"][0]["in_vector"] == [0.0, -1.0] and g2["cameras"][0]["in_vector"] == [0.0, 1.0]
    import json

    gt1 = json.loads((tmp_path / "G1" / "ground_truth.json").read_text())
    gt2 = json.loads((tmp_path / "G2" / "ground_truth.json").read_text())
    assert {v["direction"] for v in gt2["vehicles"]} == {"OUT"}
    assert "MH20EE7777" in {v["plate"] for v in gt1["vehicles"]}  # wrong-way rider only at the entry
    assert "MH20EE7777" not in {v["plate"] for v in gt2["vehicles"]}
    exits = {v["plate"]: v["cross_ms"] for v in gt2["vehicles"]}
    for v in gt1["vehicles"]:
        if v["plate"] in exits:
            assert exits[v["plate"]] > v["cross_ms"]
