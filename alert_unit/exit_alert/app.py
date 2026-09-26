"""Wires the pieces together: WebSocket thread -> queue -> UI loop (model, lights, renderer)."""
from __future__ import annotations

import argparse
import logging
import os
import queue
import signal
import sys
import threading
import time
from typing import Any, Optional

from .config import Config, load_config
from .display_state import DisplayModel
from .net import Backoff, Heartbeat, ImageCache, WsLink
from .relay import LightController, make_backend

log = logging.getLogger("exit_alert")


class AlertUnit:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        t = cfg.timing
        self.model = DisplayModel(max_slots=t.max_slots, red_hold=t.red_hold_s, green_hold=t.green_hold_s,
                                  neutral_hold=t.neutral_hold_s, warn_days=t.pass_expiry_warn_days)
        self.inbox: "queue.Queue[tuple[str, Any]]" = queue.Queue()
        self.lights = LightController(make_backend(cfg.pins, cfg.mock_gpio), cfg.pins, buzzer_s=t.buzzer_s)
        self.images = ImageCache(cfg.edge_url, cfg.device_key)
        self.display_ok = True
        self.stop_event = threading.Event()
        self.threads: list[threading.Thread] = []
        self.link: Optional[WsLink] = None
        self.heartbeat: Optional[Heartbeat] = None
        self.feeder = None
        self.exits_seen = 0
        self.reds_seen = 0

    # ------------------------------------------------------------------ inbound (any thread)
    def post_exit(self, data: dict) -> None:
        self.inbox.put(("exit", data))

    def post_status(self, ok: bool) -> None:
        self.inbox.put(("status", ok))

    # ------------------------------------------------------------------ one UI-loop step (main thread)
    def step(self, now: float) -> None:
        while True:
            try:
                kind, val = self.inbox.get_nowait()
            except queue.Empty:
                break
            if kind == "status":
                self.model.connected = bool(val)
                log.info("edge link %s", "UP" if val else "DOWN")
            elif kind == "exit":
                if val.get("gate_id") and val["gate_id"] != self.cfg.gate_id:
                    continue
                eff = self.model.on_exit(val, now)
                self.exits_seen += 1
                if eff.buzz:
                    self.reds_seen += 1
                    self.lights.buzz(now)
                for path in eff.fetch_images:
                    if not path.startswith("demo:"):
                        self.images.fetch_async(path)
        # a lost connection never turns the light red: RED comes only from a RED card on screen
        self.lights.update(now, self.model.light(now))

    def metrics(self) -> dict:
        return {"ws_connected": self.model.connected, "display_ok": self.display_ok, "exits_seen": self.exits_seen,
                "reds_seen": self.reds_seen, "light": self.lights.colour, "demo": self.cfg.demo}

    # ------------------------------------------------------------------ lifecycle
    def _spawn(self, name: str, target) -> None:
        th = threading.Thread(target=target, name=name, daemon=True)
        th.start()
        self.threads.append(th)

    def start_background(self) -> None:
        cfg = self.cfg
        if cfg.demo:
            from .demo import DemoFeeder

            self.model.connected = True
            self.feeder = DemoFeeder(self.post_exit, cfg.gate_id)
            self._spawn("demo", self.feeder.run)
            return
        self.link = WsLink(cfg.ws_url, self.post_exit, self.post_status, ping_interval=cfg.timing.ping_interval_s,
                           backoff=Backoff(cfg.timing.backoff_initial_s, cfg.timing.backoff_max_s))
        self._spawn("ws", self.link.run)
        self.heartbeat = Heartbeat(cfg.edge_url, cfg.device_key, cfg.device_id, cfg.gate_id, self.metrics,
                                   interval=cfg.timing.heartbeat_interval_s)
        self._spawn("heartbeat", self.heartbeat.run)

    def stop(self) -> None:
        self.stop_event.set()
        for part in (self.link, self.heartbeat, self.feeder):
            if part is not None:
                part.stop()
        self.lights.close()

    def run(self, screenshot: Optional[str] = None, run_seconds: Optional[float] = None) -> int:
        cfg = self.cfg
        headless = cfg.display.mode == "headless"
        if headless or screenshot:
            os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        import pygame

        from .ui import Renderer, open_display

        pygame.display.init()
        pygame.font.init()  # not pygame.init(): no audio device on the Pi
        if headless or screenshot:
            surf = pygame.Surface((cfg.display.width, cfg.display.height))
            screen = None
        else:
            screen = surf = open_display(cfg.display.mode, cfg.display.width, cfg.display.height)
        title = cfg.display.title if cfg.display.title != "Exit" else f"Exit - Gate {cfg.gate_id}"
        renderer = Renderer(cfg.display.font_path, title, cfg.timing.pass_expiry_warn_days, self.images.get,
                            cfg.display.show_images)
        self.start_background()
        clock = pygame.time.Clock()
        started = time.monotonic()
        try:
            while not self.stop_event.is_set():
                now = time.monotonic()
                if screen is not None:
                    for ev in pygame.event.get():
                        if ev.type == pygame.QUIT or (ev.type == pygame.KEYDOWN and ev.key in (pygame.K_ESCAPE, pygame.K_q)):
                            self.stop_event.set()
                self.step(now)
                try:
                    renderer.render(surf, self.model, now)
                    if screen is not None:
                        pygame.display.flip()
                    self.display_ok = True
                except pygame.error as exc:  # display lost (HDMI unplugged etc.); keep lights working
                    if self.display_ok:
                        log.error("display error: %s", exc)
                    self.display_ok = False
                if screenshot and now - started >= (run_seconds or 1.0):
                    pygame.image.save(surf, screenshot)
                    log.info("screenshot saved to %s", screenshot)
                    break
                if run_seconds and not screenshot and now - started >= run_seconds:
                    break
                clock.tick(cfg.display.fps)
        finally:
            self.stop()
            pygame.quit()
        return 0


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="exit_alert", description="Exit alert unit (display + tower light)")
    ap.add_argument("-c", "--config", help="YAML config (default /etc/alert-unit/config.yaml or ./config.yaml)")
    ap.add_argument("--edge-url")
    ap.add_argument("--device-key")
    ap.add_argument("--gate")
    ap.add_argument("--device-id")
    ap.add_argument("--demo", action="store_true", help="feed fake exits locally (no edge server)")
    ap.add_argument("--mock-gpio", action="store_true", help="never touch GPIO (default: auto-detect Pi)")
    ap.add_argument("--windowed", action="store_true")
    ap.add_argument("--headless", action="store_true", help="no display (lights + logs only)")
    ap.add_argument("--screenshot", metavar="PNG", help="render off-screen, save a PNG and exit")
    ap.add_argument("--run-seconds", type=float, help="exit after N seconds (testing)")
    ap.add_argument("-v", "--verbose", action="store_true")
    return ap.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    a = parse_args(argv)
    ov: dict[str, Any] = {}
    for k, v in (("edge_url", a.edge_url), ("device_key", a.device_key), ("gate_id", a.gate), ("device_id", a.device_id)):
        if v:
            ov[k] = v
    if a.demo:
        ov["demo"] = True
    if a.mock_gpio or a.demo:
        ov["mock_gpio"] = True
    if a.windowed:
        ov.setdefault("display", {})["mode"] = "windowed"
    if a.headless:
        ov.setdefault("display", {})["mode"] = "headless"
    cfg = load_config(a.config, overrides=ov)
    logging.basicConfig(level=logging.DEBUG if a.verbose else getattr(logging, cfg.log_level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    unit = AlertUnit(cfg)

    def _term(signum, _frame):
        log.info("signal %s: shutting down (light left green)", signum)
        unit.stop_event.set()

    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)
    log.info("alert unit %s gate=%s edge=%s demo=%s", cfg.device_id, cfg.gate_id, cfg.edge_url, cfg.demo)
    return unit.run(screenshot=a.screenshot, run_seconds=a.run_seconds)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
