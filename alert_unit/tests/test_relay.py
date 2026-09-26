from exit_alert.config import PinConfig
from exit_alert.display_state import GREEN, RED
from exit_alert.relay import GpiozeroBackend, LightController, MockBackend, make_backend


def ctl(**pins):
    p = PinConfig(**pins)
    b = MockBackend(p)
    return LightController(b, p, buzzer_s=0.5), b


def test_starts_green():
    c, b = ctl()
    assert b.state == {"red": False, "green": True, "buzzer": False}
    assert c.colour == GREEN


def test_red_then_green():
    c, b = ctl()
    c.update(0, RED)
    assert b.state["red"] and not b.state["green"]
    c.update(1, GREEN)
    assert not b.state["red"] and b.state["green"]


def test_buzzer_is_short_and_extends():
    c, b = ctl()
    c.buzz(10.0)
    assert b.state["buzzer"]
    c.update(10.4)
    assert b.state["buzzer"]
    c.buzz(10.4)  # second red while buzzing extends, no re-trigger
    c.update(10.6)
    assert b.state["buzzer"]
    c.update(10.91)
    assert not b.state["buzzer"]
    assert b.history.count(("buzzer", True)) == 1


def test_no_buzzer_channel():
    c, b = ctl(buzzer=None)
    c.buzz(0)
    assert not b.state["buzzer"] and not c.buzzing


def test_green_on_nc_inverts_green_channel():
    c, b = ctl(green_on_nc=True)
    assert b.state["green"] is False  # released relay => NC contact closed => lamp lit
    c.update(0, RED)
    assert b.state["green"] is True and b.state["red"] is True


def test_close_leaves_green_and_silent():
    c, b = ctl()
    c.update(0, RED)
    c.buzz(0)
    c.close()
    assert b.state == {"red": False, "green": True, "buzzer": False}
    assert b.closed


def test_make_backend_mock_and_autodetect(monkeypatch):
    p = PinConfig()
    assert isinstance(make_backend(p, True), MockBackend)
    monkeypatch.setattr("exit_alert.relay.is_raspberry_pi", lambda: False)
    assert isinstance(make_backend(p, None), MockBackend)


def test_gpiozero_backend_with_mock_pin_factory():
    import pytest

    gz = pytest.importorskip("gpiozero")
    from gpiozero.pins.mock import MockFactory

    gz.Device.pin_factory = MockFactory()
    p = PinConfig(red=17, green=27, buzzer=22, active_low=True)
    be = GpiozeroBackend(p)
    lc = LightController(be, p)
    # active-low: energised relay => pin driven low
    assert be.devices["green"].pin.state == 0 and be.devices["red"].pin.state == 1
    lc.update(0, RED)
    assert be.devices["red"].pin.state == 0 and be.devices["green"].pin.state == 1
    lc.close()


def test_make_backend_falls_back_when_gpio_missing(monkeypatch):
    import builtins

    real = builtins.__import__

    def fake(name, *a, **k):
        if name.startswith("gpiozero"):
            raise ImportError("no gpiozero")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)
    assert isinstance(make_backend(PinConfig(), False), MockBackend)
