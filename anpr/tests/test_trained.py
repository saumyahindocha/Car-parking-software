"""Our own synthetic-trained plate models (models/plate_finder.onnx + models/plate_reader.onnx)."""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

from anpr_service.config import TrainedConfig
from anpr_service.recognizers.trained import PlateReader, TrainedRecognizer, resolve_model, two_rows

ort = pytest.importorskip("onnxruntime")
CFG = TrainedConfig()
if not (resolve_model(CFG.reader_model).exists() and resolve_model(CFG.finder_model).exists()):
    pytest.skip("trained models not present", allow_module_level=True)


def test_reader_reads_generated_plates() -> None:
    from training import platesynth as ps

    reader = PlateReader(resolve_model(CFG.reader_model))
    rng = random.Random(4242)
    ok = n = 0
    for _ in range(60):
        x, text, _meta = ps.ocr_sample(rng, hard=0.5)
        crop = cv2.cvtColor(x.astype(np.uint8), cv2.COLOR_GRAY2BGR)
        res = reader.read(crop)
        n += 1
        ok += res is not None and res.text == text
    assert ok / n >= 0.75  # single frame; the pipeline votes over many frames


def test_read_result_shape() -> None:
    from anpr_service.synth import render_plate

    rec = TrainedRecognizer(CFG)
    plate = render_plate("MH12AB1234", "single")
    res = rec.read_plate(plate)
    assert res is not None
    assert len(res.char_confs) == len(res.text) == len(res.alternatives or [])
    assert all(0.0 <= c <= 1.0 for c in res.char_confs)
    assert all(alt and alt[0][0] == ch for ch, alt in zip(res.text, res.alternatives or []))
    assert two_rows(np.zeros((50, 200, 3), np.uint8)) == 1
    assert two_rows(np.zeros((60, 100, 3), np.uint8)) == 2


def test_finder_locates_plates_in_frame() -> None:
    from anpr_service.synth import render_plate

    rec = TrainedRecognizer(CFG)
    frame = np.full((720, 1280, 3), 70, np.uint8)
    cv2.randn(frame, (70, 70, 70), (12, 12, 12))
    plate = render_plate("KA05MN4321", "single")
    ph, pw = plate.shape[:2]
    scale = 110 / pw
    plate = cv2.resize(plate, (110, int(ph * scale)))
    y, x = 420, 600
    frame[y:y + plate.shape[0], x:x + plate.shape[1]] = plate
    found = rec.find_plates(frame, (0, 0, 1280, 720))
    assert found, "no plate found"
    best = max(found, key=lambda p: p.score)
    cx, cy = (best.box[0] + best.box[2]) / 2, (best.box[1] + best.box[3]) / 2
    assert abs(cx - (x + plate.shape[1] / 2)) < 15 and abs(cy - (y + plate.shape[0] / 2)) < 12


def test_trained_replay_end_to_end(synthetic_gate: tuple[Path, dict[str, Any]], tmp_path: Path) -> None:
    """The demo gate scene (a different renderer from the training data) read by the trained engine."""
    import httpx

    from anpr_service.backend import BackendClient
    from anpr_service.runner import InlineReplayRunner
    from test_e2e_replay import START, _config

    clip_dir, gt = synthetic_gate
    cfg = _config(clip_dir, gt, tmp_path)
    cfg.recognizer.kind = "trained"

    sink = BackendClient(cfg.backend, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"status": "ok"})))
    result = InlineReplayRunner(cfg, sink=sink, start_epoch_ms=START, drain_timeout_s=5).run()
    readable = {v["plate"] for v in gt["vehicles"] if not v["unreadable"]}
    reads = {e["plate"] for e in result.events if e["status"] == "READ"}
    assert len(reads & readable) >= len(readable) - 1, (reads, readable)
    assert len(result.events) <= len(gt["vehicles"]) + 1
