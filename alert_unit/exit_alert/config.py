"""Configuration: defaults < YAML file < environment variables (ALERT_*) < command-line flags."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Optional

DEFAULT_PATHS = ("/etc/alert-unit/config.yaml", "./config.yaml")


@dataclass
class PinConfig:
    """BCM GPIO numbers for the relay channels. `buzzer` is optional (None = no buzzer channel)."""

    red: int = 17
    green: int = 27
    buzzer: Optional[int] = 22
    # Most cheap opto-isolated relay boards switch ON when the input is pulled LOW.
    active_low: bool = True
    # When the green lamp is wired through the relay's normally-closed contact, a dead/rebooting Pi
    # (relay de-energised) still shows GREEN: traffic is never stopped by a fault. Recommended.
    green_on_nc: bool = False


@dataclass
class TimingConfig:
    red_hold_s: float = 5.0          # red light + amount due shown this long
    green_hold_s: float = 6.0        # a "Thank you" card stays this long before the idle screen
    neutral_hold_s: float = 6.0
    buzzer_s: float = 0.6            # short beep on RED
    max_slots: int = 2               # vehicles shown side by side (2 fits a two-bike gate; 3 allowed)
    ping_interval_s: float = 10.0
    heartbeat_interval_s: float = 15.0
    backoff_initial_s: float = 1.0
    backoff_max_s: float = 30.0
    pass_expiry_warn_days: int = 5


@dataclass
class DisplayConfig:
    mode: str = "fullscreen"         # fullscreen | windowed | headless
    width: int = 1920
    height: int = 1080
    fps: int = 20
    font_path: Optional[str] = None  # a TTF (e.g. DejaVuSans-Bold / NotoSans-Bold); None = pygame default
    title: str = "Exit"              # shown in the top bar, e.g. "Exit Gate 2"
    show_images: bool = True


@dataclass
class Config:
    edge_url: str = "http://edge.local:8000"
    device_key: str = "dev-device-key"
    gate_id: str = "G2"
    device_id: str = ""
    mock_gpio: Optional[bool] = None  # None = auto-detect (mock when not on a Raspberry Pi)
    demo: bool = False
    log_level: str = "INFO"
    pins: PinConfig = field(default_factory=PinConfig)
    timing: TimingConfig = field(default_factory=TimingConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)

    # ------------------------------------------------------------------ derived
    @property
    def ws_url(self) -> str:
        base = self.edge_url.rstrip("/")
        if base.startswith("https://"):
            base = "wss://" + base[len("https://"):]
        elif base.startswith("http://"):
            base = "ws://" + base[len("http://"):]
        from urllib.parse import quote

        return f"{base}/ws/device?key={quote(self.device_key)}&gate_id={quote(self.gate_id)}"


_SECTIONS = {"pins": PinConfig, "timing": TimingConfig, "display": DisplayConfig}


def _coerce(value: Any, current: Any) -> Any:
    if isinstance(value, str):
        low = value.strip().lower()
        if isinstance(current, bool) or current is None and low in ("true", "false", "yes", "no", "1", "0"):
            return low in ("1", "true", "yes", "on")
        if low in ("", "none", "null"):
            return None
        if isinstance(current, int) and not isinstance(current, bool):
            return int(value)
        if isinstance(current, float):
            return float(value)
    return value


def _apply(obj: Any, data: dict[str, Any]) -> None:
    names = {f.name for f in fields(obj)}
    for k, v in (data or {}).items():
        if k not in names:
            raise ValueError(f"unknown config key: {k}")
        cur = getattr(obj, k)
        if k in _SECTIONS and isinstance(v, dict):
            _apply(cur, v)
        else:
            setattr(obj, k, _coerce(v, cur))


def env_overrides(environ: Optional[dict[str, str]] = None) -> dict[str, Any]:
    """ALERT_EDGE_URL, ALERT_DEVICE_KEY, ... and nested ALERT_PINS__RED, ALERT_TIMING__RED_HOLD_S, ..."""
    env = os.environ if environ is None else environ
    out: dict[str, Any] = {}
    for key, val in env.items():
        if not key.startswith("ALERT_"):
            continue
        parts = key[len("ALERT_"):].lower().split("__")
        node = out
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = val
    return out


def load_config(path: Optional[str] = None, environ: Optional[dict[str, str]] = None,
                overrides: Optional[dict[str, Any]] = None) -> Config:
    cfg = Config()
    candidates = [path] if path else [p for p in DEFAULT_PATHS]
    for p in candidates:
        if p and Path(p).is_file():
            import yaml

            with open(p, encoding="utf-8") as fh:
                _apply(cfg, yaml.safe_load(fh) or {})
            break
        if path:
            raise FileNotFoundError(path)
    _apply(cfg, env_overrides(environ))
    _apply(cfg, overrides or {})
    if not cfg.device_id:
        cfg.device_id = f"AU-{cfg.gate_id}"
    if cfg.timing.max_slots < 1 or cfg.timing.max_slots > 3:
        raise ValueError("timing.max_slots must be 1..3")
    if cfg.display.mode not in ("fullscreen", "windowed", "headless"):
        raise ValueError("display.mode must be fullscreen, windowed or headless")
    return cfg
