from __future__ import annotations

from pathlib import Path

import pytest

from anpr_service.config import ServiceConfig, apply_remote_config, load_config
from anpr_service.types import CameraRole, CameraSide, GateDirection

EXAMPLE = Path(__file__).resolve().parents[1] / "config" / "site.example.yaml"


def test_example_config_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANPR_API_KEY", "k-123")
    monkeypatch.setenv("CAMERA_PASSWORD", "pw")
    monkeypatch.delenv("BACKEND_URL", raising=False)
    monkeypatch.delenv("IMAGE_ROOT", raising=False)
    cfg = load_config(EXAMPLE)
    assert [g.id for g in cfg.gates] == ["G1", "G2"]
    assert cfg.backend.api_key == "k-123"
    assert cfg.backend.url == "http://backend:8000"
    for g in cfg.gates:
        assert [c.role for c in g.cameras] == [CameraRole.ANPR, CameraRole.ANPR, CameraRole.OVERVIEW]
        assert len(g.anpr_cameras()) == 2 and len(g.overview_cameras()) == 1
    _g, cam = cfg.camera("G1-L")
    assert "pw@" in (cam.rtsp_url or "")
    assert cam.effective_side == CameraSide.LEFT
    assert cam.effective_gate_span == (0.0, 0.65)
    assert cfg.gate("G2").direction == GateDirection.OUT


def test_env_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BACKEND_URL", "http://edge:9000")
    monkeypatch.setenv("IMAGE_ROOT", str(tmp_path))
    cfg = load_config(EXAMPLE)
    assert cfg.backend.url == "http://edge:9000"
    assert cfg.image_root == str(tmp_path)


def test_side_inferred_from_camera_id() -> None:
    cfg = ServiceConfig.model_validate({"gates": [{"id": "G9", "cameras": [
        {"id": "G9-L"}, {"id": "G9-R"}, {"id": "G9-O", "role": "overview"}]}]})
    g = cfg.gate("G9")
    assert [c.effective_side for c in g.cameras] == [CameraSide.LEFT, CameraSide.RIGHT, CameraSide.OVERVIEW]
    assert g.cameras[1].effective_gate_span == (0.35, 1.0)


def test_remote_config_overlay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BACKEND_URL", raising=False)
    cfg = load_config(EXAMPLE)
    remote = {
        "gates": [
            {"id": "G1", "name": "North gate", "direction": "BOTH", "cameras": [
                {"id": "G1-L", "role": "ANPR", "rtsp_url": "rtsp://new/1", "roi": [[0, 0], [100, 0], [100, 100]],
                 "capture_line": [[0, 50], [100, 50]], "in_vector": [0, 1]},
                {"id": "G1-X", "role": "OVERVIEW", "rtsp_url": "rtsp://new/x"},
            ]},
            {"id": "G3", "name": "New gate", "direction": "IN", "cameras": []},
        ],
        "settings": {"merge_window_s": 12, "dedupe_window_s": 90, "min_confidence": 0.7, "unknown": 1},
    }
    new = apply_remote_config(cfg, remote)
    g1 = new.gate("G1")
    assert g1.direction == GateDirection.BOTH and g1.name == "North gate"
    _g, cam = new.camera("G1-L")
    assert cam.rtsp_url == "rtsp://new/1" and cam.in_vector == [0, 1]
    assert cam.gate_span == [0.0, 0.65]  # local-only field kept
    assert new.camera("G1-X")[1].role == CameraRole.OVERVIEW
    assert new.gate("G3").direction == GateDirection.IN
    assert new.settings.merge_window_s == 12 and new.settings.dedupe_window_s == 90
    assert new.settings.min_confidence == 0.7
    assert new.recognizer.kind == cfg.recognizer.kind
    # the original is not mutated
    assert cfg.gate("G1").direction == GateDirection.IN


def test_redact_url() -> None:
    from anpr_service.config import redact_url

    assert redact_url("rtsp://anpr:s3cret@10.0.0.1:554/ch1") == "rtsp://anpr:***@10.0.0.1:554/ch1"
    assert redact_url("rtsp://10.0.0.1/ch1") == "rtsp://10.0.0.1/ch1"
    assert redact_url(None) == ""
