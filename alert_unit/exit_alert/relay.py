"""Tower light + buzzer through a relay board.

`OutputBackend` is the thin GPIO layer (gpiozero on a Pi, an in-memory mock elsewhere);
`LightController` holds the logic (which channels for which colour, timed buzzer, fail-safe green)
and is driven by `update(now)` from the UI loop, so it needs no threads or timers of its own.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Protocol

from .config import PinConfig
from .display_state import GREEN, RED

log = logging.getLogger(__name__)


class OutputBackend(Protocol):
    def set(self, name: str, energised: bool) -> None: ...

    def close(self) -> None: ...


class MockBackend:
    """Records relay states; used on laptops, in demo mode and in tests."""

    def __init__(self, pins: PinConfig):
        self.pins = pins
        self.state: dict[str, bool] = {"red": False, "green": False, "buzzer": False}
        self.history: list[tuple[str, bool]] = []
        self.closed = False

    def set(self, name: str, energised: bool) -> None:
        if self.state.get(name) != energised:
            self.history.append((name, energised))
            log.debug("mock relay %s -> %s", name, "ON" if energised else "off")
        self.state[name] = energised

    def close(self) -> None:
        self.closed = True


class GpiozeroBackend:
    """Relay inputs driven by gpiozero OutputDevice (works with the Pi 5's lgpio pin factory)."""

    def __init__(self, pins: PinConfig):
        from gpiozero import OutputDevice  # imported lazily: not installed on dev machines

        self.devices = {}
        for name in ("red", "green", "buzzer"):
            pin = getattr(pins, name)
            if pin is None:
                continue
            self.devices[name] = OutputDevice(pin, active_high=not pins.active_low, initial_value=False)

    def set(self, name: str, energised: bool) -> None:
        dev = self.devices.get(name)
        if dev is None:
            return
        if energised:
            dev.on()
        else:
            dev.off()

    def close(self) -> None:
        for dev in self.devices.values():
            try:
                dev.off()
                dev.close()
            except Exception:  # pragma: no cover - hardware teardown
                pass


def is_raspberry_pi() -> bool:
    try:
        model = Path("/proc/device-tree/model").read_text(errors="ignore")
    except OSError:
        return False
    return "Raspberry Pi" in model


def make_backend(pins: PinConfig, mock: Optional[bool]) -> OutputBackend:
    """mock=None auto-detects: real GPIO only on a Raspberry Pi with gpiozero importable."""
    if mock is None:
        mock = not is_raspberry_pi()
    if mock:
        log.info("GPIO: mock backend (no relays driven)")
        return MockBackend(pins)
    try:
        backend = GpiozeroBackend(pins)
        log.info("GPIO: gpiozero red=%s green=%s buzzer=%s active_low=%s", pins.red, pins.green, pins.buzzer,
                 pins.active_low)
        return backend
    except Exception as exc:  # gpiozero missing / pins busy: keep the display working
        log.error("GPIO unavailable (%s); falling back to mock relays", exc)
        return MockBackend(pins)


class LightController:
    """Maps colour -> relay channels. Fail-safe: startup, shutdown and errors all leave GREEN on."""

    def __init__(self, backend: OutputBackend, pins: PinConfig, buzzer_s: float = 0.6):
        self.backend = backend
        self.pins = pins
        self.buzzer_s = buzzer_s
        self.colour: Optional[str] = None
        self.buzz_until = 0.0
        self.buzzing = False
        self.show(GREEN)

    def _green(self, lit: bool) -> None:
        # green on the NC contact: lamp is lit while the relay is released
        self.backend.set("green", (not lit) if self.pins.green_on_nc else lit)

    def show(self, colour: str) -> None:
        if colour == self.colour:
            return
        if colour == RED:
            self._green(False)
            self.backend.set("red", True)
        else:
            self.backend.set("red", False)
            self._green(True)
            colour = GREEN
        self.colour = colour

    def buzz(self, now: float, seconds: Optional[float] = None) -> None:
        if self.pins.buzzer is None:
            return
        self.buzz_until = max(self.buzz_until, now + (self.buzzer_s if seconds is None else seconds))
        if not self.buzzing:
            self.backend.set("buzzer", True)
            self.buzzing = True

    def update(self, now: float, colour: Optional[str] = None) -> None:
        if colour is not None:
            self.show(colour)
        if self.buzzing and now >= self.buzz_until:
            self.backend.set("buzzer", False)
            self.buzzing = False

    def close(self) -> None:
        """Leave the lane GREEN and the buzzer silent, then release the pins.

        Releasing the pins de-energises every relay: with green on a normally-open contact the lamp
        then goes dark (still never red); wiring green on the NC contact keeps it lit (see README).
        """
        try:
            self.backend.set("buzzer", False)
            self.backend.set("red", False)
            self._green(True)
        finally:
            self.backend.close()
