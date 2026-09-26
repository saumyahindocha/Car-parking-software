import pygame
import pytest

from exit_alert.app import AlertUnit, main
from exit_alert.config import load_config
from exit_alert.display_state import GREEN, RED, DisplayModel
from exit_alert.relay import MockBackend


def test_config_yaml_env_and_overrides(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("edge_url: https://edge.site:8443\ngate_id: G1\npins:\n  red: 5\n  active_low: false\n"
                 "timing:\n  red_hold_s: 4\n")
    cfg = load_config(str(p), environ={"ALERT_DEVICE_KEY": "secret", "ALERT_PINS__BUZZER": "none",
                                       "ALERT_TIMING__MAX_SLOTS": "3", "ALERT_MOCK_GPIO": "true"},
                      overrides={"display": {"mode": "windowed"}})
    assert cfg.gate_id == "G1" and cfg.device_id == "AU-G1"
    assert cfg.pins.red == 5 and cfg.pins.active_low is False and cfg.pins.buzzer is None
    assert cfg.timing.red_hold_s == 4 and cfg.timing.max_slots == 3
    assert cfg.mock_gpio is True and cfg.display.mode == "windowed"
    assert cfg.ws_url == "wss://edge.site:8443/ws/device?key=secret&gate_id=G1"
    with pytest.raises(ValueError):
        load_config(str(p), environ={"ALERT_BOGUS": "1"})
    with pytest.raises(FileNotFoundError):
        load_config(str(tmp_path / "missing.yaml"))


def unit(**ov):
    cfg = load_config(None, environ={}, overrides={"mock_gpio": True, "gate_id": "G2", **ov})
    return AlertUnit(cfg)


def test_step_drives_lights_from_messages():
    u = unit()
    assert isinstance(u.lights.backend, MockBackend)
    u.post_status(True)
    u.post_exit({"gate_id": "G2", "event_id": 1, "state": "RED", "plate": "MH12AB1234", "amount_due_paise": 2000,
                 "plate_image": None})
    u.step(100.0)
    b = u.lights.backend
    assert u.model.connected and b.state["red"] and b.state["buzzer"]
    u.step(100.7)
    assert not b.state["buzzer"]  # short beep
    u.step(105.1)
    assert b.state["green"] and not b.state["red"]  # back to green after ~5 s
    # other gate's messages are ignored; losing the link keeps the light green
    u.post_exit({"gate_id": "G1", "event_id": 2, "state": "RED"})
    u.post_status(False)
    u.step(106)
    assert b.state["green"] and not u.model.connected and u.model.visible(106) == []
    assert u.metrics()["ws_connected"] is False and u.reds_seen == 1
    u.stop()
    assert b.closed and b.state["green"]


def test_renderer_draws_red_and_green_side_by_side():
    from exit_alert.ui import COLOURS, Renderer

    pygame.display.init()
    pygame.font.init()
    r = Renderer(title="Exit - Gate G2", image_source=lambda p: None)
    m = DisplayModel(connected=True)
    m.on_exit({"event_id": 1, "state": "GREEN", "display_plate": "MH 12 AB 1234", "pass_valid_till": "2026-10-01",
               "pass_days_left": 2}, 0)
    m.on_exit({"event_id": 2, "state": "RED", "display_plate": "MH 14 CD 5678", "amount_due_paise": 4500,
               "plate_image": "demo:MH14CD5678"}, 0.1)
    s = pygame.Surface((1280, 720))
    r.render(s, m, 0.75)  # odd half-second: steady red (no pulse)
    assert s.get_at((20, 700))[:3] == COLOURS[GREEN]
    assert s.get_at((1260, 700))[:3] == COLOURS[RED]
    m2 = DisplayModel()
    r.render(s, m2, 0)  # idle + offline pill
    assert s.get_at((1270, 700))[:3] != (0, 0, 0)


def test_demo_screenshot_cli(tmp_path):
    out = tmp_path / "shot.png"
    assert main(["--demo", "--screenshot", str(out), "--run-seconds", "0.3"]) == 0
    assert out.stat().st_size > 1000
