"""pygame renderer: large, high-contrast layout readable outdoors on a 24-32" high-brightness panel.

The renderer draws onto any Surface (the fullscreen display, or an off-screen Surface in tests and
for `--screenshot`), from the pure `DisplayModel` state.
"""
from __future__ import annotations

import io
import os
from datetime import datetime
from typing import Callable, Optional

import pygame

from .display_state import GREEN, NEUTRAL, RED, Card, DisplayModel

# colours chosen for daylight contrast (white text on saturated backgrounds)
BG = (8, 12, 10)
BAR = (20, 24, 22)
COLOURS = {GREEN: (0, 122, 51), RED: (200, 16, 30), NEUTRAL: (48, 60, 70)}
IDLE = (0, 96, 40)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
YELLOW = (255, 214, 0)
AMBER = (255, 160, 0)
PLATE_BG = (250, 250, 250)

FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
)


def find_font(explicit: Optional[str]) -> Optional[str]:
    if explicit and os.path.isfile(explicit):
        return explicit
    for p in FONT_CANDIDATES:
        if os.path.isfile(p):
            return p
    return None


class Fonts:
    def __init__(self, path: Optional[str]):
        self.path = path
        self._cache: dict[int, pygame.font.Font] = {}
        probe = self.get(24)
        m = probe.metrics("₹")
        self.has_rupee = bool(m) and m[0] is not None and path is not None

    def get(self, size: int) -> pygame.font.Font:
        size = max(8, int(size))
        f = self._cache.get(size)
        if f is None:
            f = pygame.font.Font(self.path, size)
            self._cache[size] = f
        return f

    def fit(self, text: str, max_w: int, size: int, min_size: int = 14) -> pygame.font.Font:
        while size > min_size and self.get(size).size(text)[0] > max_w:
            size = int(size * 0.9)
        return self.get(size)

    def money(self, text: str) -> str:
        return text if self.has_rupee else text.replace("₹", "Rs ")


def synthetic_plate(text: str, size: tuple[int, int] = (400, 120)) -> pygame.Surface:
    """Stand-in plate image for --demo (no camera)."""
    s = pygame.Surface(size)
    s.fill((235, 235, 225))
    pygame.draw.rect(s, BLACK, s.get_rect(), 6)
    fs = int(size[1] * 0.6)
    f = pygame.font.Font(find_font(None), fs)
    while f.size(text)[0] > size[0] - 30 and fs > 10:
        fs = int(fs * 0.9)
        f = pygame.font.Font(find_font(None), fs)
    t = f.render(text, True, BLACK)
    s.blit(t, t.get_rect(center=s.get_rect().center))
    return s


class Renderer:
    def __init__(self, font_path: Optional[str] = None, title: str = "Exit", warn_days: int = 5,
                 image_source: Optional[Callable[[str], Optional[bytes]]] = None, show_images: bool = True):
        self.fonts = Fonts(find_font(font_path))
        self.title = title
        self.warn_days = warn_days
        self.image_source = image_source
        self.show_images = show_images
        self._images: dict[str, Optional[pygame.Surface]] = {}

    # ------------------------------------------------------------------ images
    def image(self, path: Optional[str]) -> Optional[pygame.Surface]:
        if not path or not self.show_images:
            return None
        if path in self._images and self._images[path] is not None:
            return self._images[path]
        surf: Optional[pygame.Surface] = None
        if path.startswith("demo:"):
            surf = synthetic_plate(path[5:])
        elif self.image_source is not None:
            data = self.image_source(path)
            if data:
                try:
                    surf = pygame.image.load(io.BytesIO(data))
                except pygame.error:
                    surf = None
        if len(self._images) > 32:
            self._images.clear()
        self._images[path] = surf
        return surf

    # ------------------------------------------------------------------ drawing helpers
    def _text(self, surf: pygame.Surface, text: str, rect: pygame.Rect, size: int, colour=WHITE, *,
              center: bool = True, y: Optional[int] = None) -> int:
        f = self.fonts.fit(text, rect.width - 20, size)
        img = f.render(text, True, colour)
        r = img.get_rect()
        r.top = rect.top if y is None else y
        if center:
            r.centerx = rect.centerx
        else:
            r.left = rect.left + 10
        surf.blit(img, r)
        return r.bottom

    def _plate(self, surf: pygame.Surface, text: str, rect: pygame.Rect, y: int, height: int) -> int:
        f = self.fonts.fit(text, int(rect.width * 0.86), int(height * 0.78))
        img = f.render(text, True, BLACK)
        box = pygame.Rect(0, 0, min(rect.width - 24, img.get_width() + height // 2), height)
        box.centerx, box.top = rect.centerx, y
        pygame.draw.rect(surf, PLATE_BG, box, border_radius=max(6, height // 10))
        pygame.draw.rect(surf, BLACK, box, width=max(3, height // 25), border_radius=max(6, height // 10))
        surf.blit(img, img.get_rect(center=box.center))
        return box.bottom

    # ------------------------------------------------------------------ main entry
    def render(self, surf: pygame.Surface, model: DisplayModel, now: float, wall: Optional[datetime] = None) -> None:
        w, h = surf.get_size()
        surf.fill(BG)
        bar_h = max(40, h // 12)
        pygame.draw.rect(surf, BAR, (0, 0, w, bar_h))
        self._text(surf, self.title, pygame.Rect(0, 0, w // 2, bar_h), int(bar_h * 0.6), center=False,
                   y=int(bar_h * 0.18))
        clock = (wall or datetime.now()).strftime("%H:%M")
        f = self.fonts.get(int(bar_h * 0.6))
        ci = f.render(clock, True, WHITE)
        surf.blit(ci, ci.get_rect(right=w - 16, centery=bar_h // 2))

        body = pygame.Rect(0, bar_h, w, h - bar_h)
        cards = model.visible(now)
        if not cards:
            self._idle(surf, body)
        else:
            gap = max(8, w // 120)
            n = len(cards)
            cw = (body.width - gap * (n + 1)) // n
            for i, c in enumerate(cards):
                r = pygame.Rect(body.left + gap + i * (cw + gap), body.top + gap, cw, body.height - 2 * gap)
                self._card(surf, r, c, now, compact=n > 1)

        status = model.status_text()
        if status:
            sf = self.fonts.get(max(16, h // 36))
            img = sf.render(status, True, BLACK)
            pill = img.get_rect()
            pill.inflate_ip(24, 12)
            pill.bottomright = (w - 12, h - 12)
            pygame.draw.rect(surf, AMBER, pill, border_radius=pill.height // 2)
            surf.blit(img, img.get_rect(center=pill.center))

    def _idle(self, surf: pygame.Surface, body: pygame.Rect) -> None:
        pygame.draw.rect(surf, IDLE, body)
        y = body.top + body.height // 3
        y = self._text(surf, "Thank you", body, body.height // 5, y=y)
        self._text(surf, "Drive safely", body, body.height // 12, y=y + body.height // 30)

    def _card(self, surf: pygame.Surface, r: pygame.Rect, c: Card, now: float, compact: bool) -> None:
        colour = COLOURS[c.state]
        if c.is_red and int(now * 2) % 2 == 0:
            colour = (230, 30, 40)  # gentle pulse so the guard's eye is drawn to it
        pygame.draw.rect(surf, colour, r, border_radius=18)
        scale = r.height / 1000
        y = r.top + int(40 * scale)
        y = self._text(surf, c.headline(), r, int((130 if not compact else 110) * scale), y=y)
        y = self._plate(surf, c.display_plate or "—", r, y + int(30 * scale), int((190 if not compact else 160) * scale))
        y += int(30 * scale)
        lines = c.lines(self.warn_days)
        if c.is_red:
            y = self._text(surf, self.fonts.money(lines[0]), r, int(120 * scale), YELLOW, y=y)
            y = self._text(surf, lines[1], r, int(70 * scale), y=y + int(10 * scale))
        else:
            for i, line in enumerate(lines):
                col = YELLOW if i == 1 or "expires" in line else WHITE
                y = self._text(surf, line, r, int(80 * scale), col, y=y + int(8 * scale))
        if c.state in (RED, NEUTRAL):
            img = self.image(c.plate_image)
            if img is not None:
                room = pygame.Rect(r.left + 20, y + int(20 * scale), r.width - 40, r.bottom - y - int(40 * scale))
                if room.height > 40:
                    iw, ih = img.get_size()
                    k = min(room.width / iw, room.height / ih)
                    scaled = pygame.transform.smoothscale(img, (max(1, int(iw * k)), max(1, int(ih * k))))
                    surf.blit(scaled, scaled.get_rect(midtop=(room.centerx, room.top)))


def open_display(mode: str, width: int, height: int) -> pygame.Surface:
    pygame.display.init()
    pygame.font.init()
    if mode == "fullscreen":
        surf = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        pygame.mouse.set_visible(False)
    else:
        surf = pygame.display.set_mode((width, height))
    pygame.display.set_caption("Exit alert")
    return surf
