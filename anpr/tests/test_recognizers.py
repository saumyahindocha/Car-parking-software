"""ONNX output decoding, CTC, the ONNX recogniser with fake sessions, and the commercial API adapter."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import numpy as np
import pytest

from anpr_service.config import CommercialConfig, OnnxConfig
from anpr_service.recognizers import RecognizerUnavailable
from anpr_service.recognizers.commercial import CommercialApiRecognizer
from anpr_service.recognizers.ctc import ctc_greedy_decode
from anpr_service.recognizers.onnx_local import LocalOnnxRecognizer
from anpr_service.recognizers.yolo import (
    batched_nms,
    decode,
    letterbox,
    nms,
    unletterbox,
)
from anpr_service.synth import render_plate
from anpr_service.types import VehicleClass

ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def test_nms_suppresses_overlaps() -> None:
    boxes = np.array([[0, 0, 10, 10], [1, 1, 11, 11], [20, 20, 30, 30]], dtype=np.float32)
    scores = np.array([0.9, 0.8, 0.7], dtype=np.float32)
    assert nms(boxes, scores, 0.5).tolist() == [0, 2]
    # different classes never suppress each other
    assert sorted(batched_nms(boxes, scores, np.array([0, 1, 0]), 0.5).tolist()) == [0, 1, 2]


def test_decode_yolov8_layout() -> None:
    n, nc = 20, 4  # real exports have N (e.g. 8400) >> channels
    out = np.zeros((1, 4 + nc, n), dtype=np.float32)
    out[0, :4, 0] = [100, 100, 40, 80]
    out[0, 4 + 0, 0] = 0.9  # BIKE
    out[0, :4, 1] = [300, 200, 20, 10]
    out[0, 4 + 3, 1] = 0.8  # PLATE
    out[0, 4 + 1, 2] = 0.1  # below threshold
    boxes, scores, cls = decode(out, 0.3)
    assert cls.tolist() == [0, 3]
    np.testing.assert_allclose(boxes[0], [80, 60, 120, 140])
    np.testing.assert_allclose(scores, [0.9, 0.8], rtol=1e-6)


def test_decode_yolov5_layout_with_objectness() -> None:
    out = np.zeros((1, 3, 5 + 2), dtype=np.float32)
    out[0, 0] = [50, 50, 10, 10, 0.9, 0.1, 0.8]
    out[0, 1] = [80, 80, 10, 10, 0.2, 0.9, 0.1]
    boxes, scores, cls = decode(out, 0.5, "yolov5")
    assert cls.tolist() == [1]
    assert scores[0] == pytest.approx(0.72)


def test_letterbox_roundtrip() -> None:
    img = np.zeros((720, 1280, 3), dtype=np.uint8)
    lb, scale, pad = letterbox(img, 640)
    assert lb.shape == (640, 640, 3)
    box = np.array([[100 * scale + pad[0], 200 * scale + pad[1], 300 * scale + pad[0], 400 * scale + pad[1]]])
    np.testing.assert_allclose(unletterbox(box, scale, pad, 1280, 720), [[100, 200, 300, 400]], atol=1e-3)


def _one_hot_logits(text: str, blank: int = 0, repeat: int = 2) -> np.ndarray:
    steps = []
    for ch in text:
        k = ALPHABET.index(ch) + 1
        steps += [k] * repeat + [blank]
    logits = np.full((len(steps), 1, len(ALPHABET) + 1), -5.0, dtype=np.float32)
    for t, k in enumerate(steps):
        logits[t, 0, k] = 5.0
    return logits


def test_ctc_greedy_decode_collapses_repeats_and_blanks() -> None:
    text, confs = ctc_greedy_decode(_one_hot_logits("MH4411"), ALPHABET)
    assert text == "MH4411"
    assert len(confs) == 6 and min(confs) > 0.99


class FakeSession:
    def __init__(self, name: str, fn: Any) -> None:
        self._name = name
        self._fn = fn
        self.calls = 0

    def get_inputs(self) -> list[Any]:
        return [SimpleNamespace(name=self._name, shape=[1, 3, 640, 640])]

    def run(self, _names: Any, feed: dict[str, np.ndarray]) -> list[np.ndarray]:
        self.calls += 1
        return [self._fn(feed[self._name])]


def test_local_onnx_recognizer_with_fake_sessions() -> None:
    rows = iter(["MH43", "AB1234"])

    def detector(x: np.ndarray) -> np.ndarray:
        assert x.shape == (1, 3, 640, 640) and x.dtype == np.float32
        out = np.zeros((1, 8, 50), dtype=np.float32)
        # 1280x720 frame -> letterboxed with scale 0.5, pad_y 140
        out[0, :4, 0] = [320, 140 + 200, 100, 200]  # bike centred at (640, 400) in the frame
        out[0, 4, 0] = 0.9
        out[0, :4, 1] = [320, 140 + 230, 30, 15]  # plate centred at (640, 460)
        out[0, 7, 1] = 0.8
        return out

    def ocr(x: np.ndarray) -> np.ndarray:
        assert x.shape == (1, 1, 32, 128)
        return _one_hot_logits(next(rows))

    sessions = {"det.onnx": FakeSession("images", detector), "ocr.onnx": FakeSession("x", ocr)}
    cfg = OnnxConfig(detector_model="det.onnx", ocr_model="ocr.onnx")
    rec = LocalOnnxRecognizer(cfg, session_factory=lambda p: sessions[p])
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    obs = rec.analyze(frame)
    assert len(obs) == 1 and obs[0].vehicle_class == VehicleClass.BIKE
    assert obs[0].plate is not None
    np.testing.assert_allclose(obs[0].plate.box, [610, 445, 670, 475], atol=1)
    import cv2

    plate = cv2.resize(render_plate("MH43AB1234", "two_line"), (160, 80))
    res = rec.read_plate(plate)
    assert res is not None and res.text == "MH43AB1234" and res.rows == 2


def test_local_onnx_recognizer_missing_runtime_or_model() -> None:
    def factory(path: str) -> Any:
        raise RecognizerUnavailable(f"model file not found: {path}")

    with pytest.raises(RecognizerUnavailable):
        LocalOnnxRecognizer(OnnxConfig(), session_factory=factory)


PR_RESPONSE = {
    "processing_time": 42.1,
    "results": [
        {
            "box": {"xmin": 600, "ymin": 440, "xmax": 680, "ymax": 480},
            "plate": "mh43ab1234",
            "region": {"code": "in", "score": 0.9},
            "score": 0.91,
            "dscore": 0.88,
            "candidates": [{"plate": "mh43ab1234", "score": 0.91}, {"plate": "mh43a81234", "score": 0.7}],
            "vehicle": {"type": "Motorcycle", "score": 0.8, "box": {"xmin": 540, "ymin": 200, "xmax": 740, "ymax": 700}},
        }
    ],
}


def test_commercial_api_adapter_parses_plate_recognizer_response() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = request.content
        return httpx.Response(201, json=PR_RESPONSE)

    rec = CommercialApiRecognizer(CommercialConfig(), api_key="tok", transport=httpx.MockTransport(handler))
    obs = rec.analyze(np.zeros((720, 1280, 3), dtype=np.uint8))
    assert seen["auth"] == "Token tok"
    assert b'name="regions"' in seen["body"] and b"\r\n\r\nin\r\n" in seen["body"]
    assert len(obs) == 1
    o = obs[0]
    assert o.vehicle_class == VehicleClass.BIKE
    assert o.box == (540.0, 200.0, 740.0, 700.0)
    assert o.plate is not None and o.plate.read is not None
    assert o.plate.read.text == "MH43AB1234"
    assert o.plate.read.confidence == pytest.approx(0.91)


def test_commercial_api_errors_are_non_fatal() -> None:
    rec = CommercialApiRecognizer(
        CommercialConfig(), api_key="tok", transport=httpx.MockTransport(lambda r: httpx.Response(429, text="slow"))
    )
    assert rec.analyze(np.zeros((10, 10, 3), dtype=np.uint8)) == []


def test_commercial_api_requires_key_for_cloud(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PLATE_RECOGNIZER_API_KEY", raising=False)
    with pytest.raises(RecognizerUnavailable):
        CommercialApiRecognizer(CommercialConfig())
    # on-prem SDK needs no token
    CommercialApiRecognizer(CommercialConfig(api_url="http://sdk:8080/v1/plate-reader/"))


def test_json_roundtrip_of_candidates() -> None:
    assert json.loads(json.dumps(PR_RESPONSE))["results"][0]["plate"] == "mh43ab1234"
