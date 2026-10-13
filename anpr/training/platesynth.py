"""Synthetic Indian number plates for training the plate reader and the plate finder.

Everything here is computer-generated: plate text follows real Indian registration formats, plates are
drawn in the styles seen on the road (HSRP, old-style private, commercial, EV, rental), in single-line and
two-line layouts, and then degraded the way a rear-facing gate camera sees them: perspective, motion and
focus blur, low resolution, dust, mud, faded paint, shadows, headlight glare, IR night images, JPEG.

Used by train_ocr.py / train_detector.py. Needs Pillow, OpenCV and NumPy, plus TrueType fonts
(on Ubuntu: apt install fonts-dejavu-core fonts-liberation fonts-freefont-ttf).
"""
from __future__ import annotations

import glob
import os
import random
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# ----------------------------------------------------------------------------- text
ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
MAX_LEN = 11  # longest Indian plate: SS DD LLL NNNN
PAD = len(ALPHABET)  # class index used for empty slots

# Registration volume is skewed; the lot is in Maharashtra, so MH dominates, neighbours next.
STATE_WEIGHTS = {
    "MH": 55, "GJ": 6, "KA": 6, "MP": 4, "TS": 3, "AP": 2, "DL": 3, "UP": 3, "RJ": 2, "GA": 2, "TN": 2,
    "KL": 1, "HR": 1, "PB": 1, "BR": 1, "WB": 1, "OD": 1, "CG": 1, "JH": 1, "UK": 1, "CH": 1, "HP": 1,
    "JK": 1, "AS": 1, "DD": 1, "DN": 1,
}
_STATES, _SW = zip(*STATE_WEIGHTS.items())
SERIES_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"  # I and O are not issued in series


def random_plate_text(rng: random.Random) -> tuple[str, list[str], str]:
    """Returns (plate, groups, kind). groups are the printed chunks, e.g. ['MH','43','AB','1234']."""
    if rng.random() < 0.04:  # Bharat series: YY BH NNNN LL
        yy = f"{rng.randint(21, 27):02d}"
        num = f"{rng.randint(1, 9999):04d}"
        tail = "".join(rng.choice(SERIES_LETTERS) for _ in range(rng.choice([1, 2, 2])))
        g = [yy, "BH", num, tail]
        return "".join(g), g, "BH"
    st = rng.choices(_STATES, _SW)[0]
    dist = rng.randint(1, 99 if st in ("MH", "UP", "KA", "GJ", "TN", "RJ", "MP") else 40)
    d = f"{dist:02d}" if rng.random() < 0.93 else str(dist % 10 or 1)
    nl = rng.choices([0, 1, 2, 3], [3, 18, 74, 5])[0]
    series = "".join(rng.choice(SERIES_LETTERS) for _ in range(nl))
    if st == "DL" and rng.random() < 0.7:  # Delhi: DL 3C AF 1234 / DL 8S AB 1234 (1-digit district + category)
        d = str(rng.randint(1, 13))
        series = rng.choice("CSEPRTVY") + "".join(rng.choice(SERIES_LETTERS) for _ in range(rng.choice([1, 2, 2])))
    num = rng.randint(1, 9999)
    num_s = f"{num:04d}"
    g = [st, d] + ([series] if series else []) + [num_s]
    return "".join(g), g, "STD"


def encode(text: str) -> list[int]:
    ids = [ALPHABET.index(c) for c in text]
    return ids + [PAD] * (MAX_LEN - len(ids))


def decode(ids) -> str:
    return "".join(ALPHABET[i] for i in ids if i != PAD)


# ----------------------------------------------------------------------------- fonts
_FONT_DIRS = [os.path.join(os.path.dirname(__file__), "fonts"), "/usr/share/fonts", os.path.expanduser("~/.fonts")]
_FONT_PREFS = [  # (filename pattern, weight): bold sans fonts look most like real plates
    ("DejaVuSans-Bold.ttf", 5), ("DejaVuSansMono-Bold.ttf", 3), ("LiberationSans-Bold.ttf", 5),
    ("LiberationMono-Bold.ttf", 2), ("FreeSansBold.ttf", 4), ("FreeMonoBold.ttf", 1), ("DejaVuSans.ttf", 1),
    ("LiberationSans-Regular.ttf", 1), ("FreeSans.ttf", 1), ("LiberationSerif-Bold.ttf", 1),
    ("DejaVuSerif-Bold.ttf", 1), ("Loma-Bold.otf", 2), ("ipag.ttf", 1), ("LiberationSansNarrow-Bold.ttf", 4),
    ("c0648bt_.pfb", 1), ("c0633bt_.pfb", 1), ("c0419bt_.pfb", 1),
]


@lru_cache(maxsize=1)
def available_fonts() -> tuple[tuple[str, int], ...]:
    found = []
    for pat, w in _FONT_PREFS:
        for d in _FONT_DIRS:
            hits = glob.glob(os.path.join(d, "**", pat), recursive=True)
            if hits:
                found.append((hits[0], w))
                break
    if not found:
        raise RuntimeError("no fonts found: apt install fonts-dejavu-core fonts-liberation fonts-freefont-ttf")
    return tuple(found)


# OpenCV's built-in stroke fonts (thin/thick line-drawn digits, like many hand-painted and
# older embossed plates). Encoded as pseudo paths "hershey:<face>:<thickness factor>".
_HERSHEY = [("hershey:0:0.06", 1), ("hershey:0:0.10", 1), ("hershey:0:0.16", 1), ("hershey:1:0.12", 1), ("hershey:2:0.10", 1),
            ("hershey:3:0.14", 1), ("hershey:4:0.12", 1)]


def plate_fonts() -> tuple[tuple[str, int], ...]:
    """Typefaces for the registration characters: TrueType fonts plus stroke fonts."""
    return available_fonts() + tuple(_HERSHEY)


@lru_cache(maxsize=256)
def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


@lru_cache(maxsize=4096)
def _glyph(path: str, size: int, ch: str) -> np.ndarray:
    """Tight alpha mask of one character."""
    if path.startswith("hershey:"):
        _, face, thick = path.split(":")
        scale = cv2.getFontScaleFromHeight(int(face), size, 1)
        th = max(1, int(round(size * float(thick))))
        (tw, tht), base = cv2.getTextSize(ch, int(face), scale, th)
        a = np.zeros((tht + base + 2 * th + 8, tw + 2 * th + 8), np.uint8)
        cv2.putText(a, ch, (th + 4, tht + th + 4), int(face), scale, 255, th, cv2.LINE_AA)
        ys, xs = np.nonzero(a > 20)
        return a[ys.min(): ys.max() + 1, xs.min(): xs.max() + 1] if len(xs) else a
    f = _font(path, size)
    l, t, r, b = f.getbbox(ch)
    img = Image.new("L", (r - l + 4, b - t + 4), 0)
    ImageDraw.Draw(img).text((2 - l, 2 - t), ch, font=f, fill=255)
    a = np.array(img)
    ys, xs = np.nonzero(a > 20)
    return a[ys.min(): ys.max() + 1, xs.min(): xs.max() + 1] if len(xs) else a


# ----------------------------------------------------------------------------- plate styles
@dataclass
class Style:
    name: str
    bg: tuple[int, int, int]  # RGB
    fg: tuple[int, int, int]
    border: bool
    ind_strip: bool


def pick_style(rng: random.Random) -> Style:
    r = rng.random()
    if r < 0.50:
        return Style("hsrp", (rng.randint(228, 255),) * 3, (rng.randint(0, 35),) * 3, True, True)
    if r < 0.80:  # old-style private plate, often slightly off-white
        w = rng.randint(200, 255)
        fg = rng.choice([(10, 10, 10), (20, 20, 20), (15, 15, 60), (120, 10, 10)])
        return Style("old", (w, w - rng.randint(0, 20), w - rng.randint(0, 35)), fg, rng.random() < 0.6, False)
    if r < 0.91:
        return Style("commercial", (rng.randint(215, 255), rng.randint(170, 215), rng.randint(0, 40)), (15, 15, 15),
                     rng.random() < 0.8, rng.random() < 0.5)
    if r < 0.97:
        return Style("ev", (rng.randint(0, 40), rng.randint(120, 170), rng.randint(40, 90)),
                     (250, 250, 250) if rng.random() < 0.8 else (250, 220, 0), True, rng.random() < 0.6)
    return Style("rental", (15, 15, 15), (250, 215, 0), True, False)


def _layout_rows(groups: list[str], kind: str, two_line: bool, rng: random.Random) -> list[str]:
    if not two_line:
        return [" ".join(groups) if rng.random() < 0.7 else "".join(groups)]
    if kind == "BH":
        return [" ".join(groups[:2]), " ".join(groups[2:])]
    if len(groups) == 4 and rng.random() < 0.25:  # MH43AB / 1234 split
        return [" ".join(groups[:3]), groups[3]]
    return [" ".join(groups[:2]), " ".join(groups[2:])]


def render_plate(rng: random.Random, text_groups: Optional[tuple[list[str], str]] = None, two_line: Optional[bool] = None,
                 style: Optional[Style] = None) -> tuple[np.ndarray, str, dict]:
    """Clean, front-on plate image (RGB uint8) and its text."""
    if text_groups is None:
        plate, groups, kind = random_plate_text(rng)
    else:
        groups, kind = text_groups
        plate = "".join(groups)
    two_line = rng.random() < 0.72 if two_line is None else two_line
    style = style or pick_style(rng)
    rows = _layout_rows(groups, kind, two_line, rng)
    fonts = available_fonts()  # decorations (IND strip, dealer text)
    pf = plate_fonts()
    fpath = rng.choices([f for f, _ in pf], [w for _, w in pf])[0]
    squeeze = rng.uniform(0.55, 0.8) if style.name == "hsrp" else rng.uniform(0.6, 1.05)

    # glyph masks per row, then plate size from content
    char_h = rng.randint(70, 90)
    row_imgs = []
    for row in rows:
        parts = []
        for ch in row:
            if ch == " ":
                parts.append(None)
                continue
            g = _glyph(fpath, int(char_h * 1.25), ch)
            g = cv2.resize(g, (max(3, int(g.shape[1] * char_h / max(g.shape[0], 1) * squeeze)), char_h),
                           interpolation=cv2.INTER_AREA)
            parts.append(g)
        gap = int(char_h * rng.uniform(0.06, 0.16))
        space = int(char_h * rng.uniform(0.25, 0.55))
        width = sum((p.shape[1] + gap) if p is not None else space for p in parts)
        canvas = np.zeros((char_h, max(width, 1)), np.uint8)
        x = 0
        for p in parts:
            if p is None:
                x += space
                continue
            canvas[:, x:x + p.shape[1]] = np.maximum(canvas[:, x:x + p.shape[1]], p)
            x += p.shape[1] + gap
        row_imgs.append(canvas[:, : max(1, x - gap)])

    text_w = max(r.shape[1] for r in row_imgs)
    line_gap = int(char_h * rng.uniform(0.18, 0.35))
    text_h = sum(r.shape[0] for r in row_imgs) + line_gap * (len(row_imgs) - 1)
    strip_w = int(char_h * 0.55) if style.ind_strip else 0
    mx, my = int(char_h * rng.uniform(0.18, 0.45)), int(char_h * rng.uniform(0.15, 0.4))
    W, H = text_w + 2 * mx + strip_w, text_h + 2 * my
    img = np.zeros((H, W, 3), np.uint8)
    img[:] = style.bg
    # text
    y = my
    for r in row_imgs:
        x = strip_w + mx + (text_w - r.shape[1]) // 2 if rng.random() < 0.8 else strip_w + mx
        a = (r.astype(np.float32) / 255.0)[..., None]
        if style.name == "hsrp":  # embossed look: faint lower-right shadow
            sh = np.zeros_like(a)
            sh[2:, 2:] = a[:-2, :-2]
            reg = img[y:y + r.shape[0], x:x + r.shape[1]].astype(np.float32)
            img[y:y + r.shape[0], x:x + r.shape[1]] = (reg * (1 - 0.35 * sh)).astype(np.uint8)
        reg = img[y:y + r.shape[0], x:x + r.shape[1]].astype(np.float32)
        img[y:y + r.shape[0], x:x + r.shape[1]] = (reg * (1 - a) + np.array(style.fg) * a).astype(np.uint8)
        y += r.shape[0] + line_gap
    # IND strip + chakra hologram
    if style.ind_strip:
        cv2.rectangle(img, (int(strip_w * 0.1), int(H * 0.08)), (int(strip_w * 0.95), int(H * 0.92)),
                      (rng.randint(0, 40), rng.randint(40, 90), rng.randint(150, 210)), -1)
        cx, cy, rr = int(strip_w * 0.52), int(H * 0.3), max(3, int(strip_w * 0.28))
        cv2.circle(img, (cx, cy), rr, (230, 230, 240), max(1, rr // 4))
        f = _font(fonts[0][0], max(8, int(strip_w * 0.32)))
        pil = Image.fromarray(img)
        d = ImageDraw.Draw(pil)
        for i, ch in enumerate("IND"):
            d.text((int(strip_w * 0.35), int(H * 0.52) + i * int(strip_w * 0.33)), ch, font=f, fill=(245, 245, 245))
        img = np.array(pil)
    if style.border:
        t = max(2, int(min(W, H) * rng.uniform(0.015, 0.035)))
        cv2.rectangle(img, (t // 2, t // 2), (W - 1 - t // 2, H - 1 - t // 2), style.fg, t)
    if style.name == "hsrp" and rng.random() < 0.7:  # hologram sticker top-left of the text area
        c = (strip_w + mx // 2 + 6, my // 2 + 6)
        cv2.ellipse(img, c, (max(4, mx // 3), max(3, my // 3)), 0, 0, 360, (200, 205, 215), -1)
    if style.name == "old" and rng.random() < 0.35:  # decorative text / sticker on old plates
        f = _font(rng.choice(fonts)[0], max(10, int(char_h * 0.22)))
        pil = Image.fromarray(img)
        word = rng.choice(["JAI MAHARASHTRA", "RAJ", "PATIL", "BOSS", "POLICE", "PRESS", "DOCTOR", "ADVOCATE", "MAHARAJ"])
        ImageDraw.Draw(pil).text((strip_w + mx, 1), word, font=f, fill=(rng.randint(100, 200), 20, 20))
        img = np.array(pil)
    for _ in range(rng.choice([0, 2, 2, 4])):  # rivets / screws
        cx, cy = rng.randint(0, W - 1), rng.choice([rng.randint(0, my), rng.randint(H - my, H - 1)])
        cv2.circle(img, (cx, cy), max(2, char_h // 16), (150, 150, 150), -1)
    meta = {"style": style.name, "rows": len(rows), "kind": kind}
    return img, plate, meta


# ----------------------------------------------------------------------------- degradations
def _motion_kernel(k: int, angle: float) -> np.ndarray:
    ker = np.zeros((k, k), np.float32)
    ker[k // 2, :] = 1.0
    m = cv2.getRotationMatrix2D((k / 2 - 0.5, k / 2 - 0.5), angle, 1.0)
    ker = cv2.warpAffine(ker, m, (k, k))
    return ker / max(ker.sum(), 1e-6)


def dirt(img: np.ndarray, rng: random.Random, amount: float) -> np.ndarray:
    """Dust film, mud splashes (heavier at the bottom), specks and scratches."""
    h, w = img.shape[:2]
    out = img.astype(np.float32)
    if amount <= 0:
        return img
    # dust film
    film = cv2.GaussianBlur(np.random.default_rng(rng.randint(0, 1 << 30)).random((max(2, h // 8), max(2, w // 8)))
                            .astype(np.float32), (0, 0), 1.5)
    film = cv2.resize(film, (w, h))
    dust = np.array([rng.randint(110, 170), rng.randint(95, 150), rng.randint(70, 120)], np.float32)
    alpha = (film * amount * 0.4)[..., None]
    out = out * (1 - alpha) + dust * alpha
    # mud splashes
    for _ in range(int(amount * rng.randint(0, 14))):
        cx = rng.randint(0, w - 1)
        cy = int(h * (rng.random() ** 0.5))  # biased to the bottom
        r = max(1, int(min(w, h) * rng.uniform(0.01, 0.08)))
        col = (rng.randint(50, 110), rng.randint(40, 90), rng.randint(25, 60))
        m = np.zeros((h, w), np.float32)
        cv2.circle(m, (cx, cy), r, 1.0, -1)
        m = cv2.GaussianBlur(m, (0, 0), max(0.5, r / 3))[..., None] * rng.uniform(0.4, 0.95)
        out = out * (1 - m) + np.array(col, np.float32) * m
    # scratches
    for _ in range(int(amount * rng.randint(0, 5))):
        p1 = (rng.randint(0, w - 1), rng.randint(0, h - 1))
        p2 = (p1[0] + rng.randint(-w // 3, w // 3), p1[1] + rng.randint(-h // 4, h // 4))
        cv2.line(out, p1, p2, (rng.randint(150, 230),) * 3, 1)
    return np.clip(out, 0, 255).astype(np.uint8)


def lighting(img: np.ndarray, rng: random.Random) -> np.ndarray:
    h, w = img.shape[:2]
    out = img.astype(np.float32)
    # overall exposure and contrast
    out = (out - 128) * rng.uniform(0.6, 1.2) + 128 + rng.uniform(-45, 35)
    # hard shadow (bike body / rider) or soft gradient
    if rng.random() < 0.35:
        pts = np.array([[rng.randint(-w, w * 2), rng.randint(-h, h * 2)] for _ in range(4)], np.int32)
        m = np.zeros((h, w), np.float32)
        cv2.fillPoly(m, [pts], 1.0)
        m = cv2.GaussianBlur(m, (0, 0), rng.uniform(0.5, 6))[..., None]
        out = out * (1 - m * rng.uniform(0.3, 0.75))
    if rng.random() < 0.4:
        gx = np.linspace(rng.uniform(0.5, 1.2), rng.uniform(0.5, 1.2), w, dtype=np.float32)[None, :, None]
        out = out * gx
    # glare / headlight bloom
    if rng.random() < 0.12:
        m = np.zeros((h, w), np.float32)
        cv2.circle(m, (rng.randint(0, w - 1), rng.randint(0, h - 1)), int(min(w, h) * rng.uniform(0.1, 0.35)), 1.0, -1)
        m = cv2.GaussianBlur(m, (0, 0), min(w, h) * 0.15)[..., None]
        out = out + m * rng.uniform(50, 150)
    # colour cast
    out = out * np.array([rng.uniform(0.85, 1.15) for _ in range(3)], np.float32)
    return np.clip(out, 0, 255).astype(np.uint8)


def ir_night(img: np.ndarray, rng: random.Random) -> np.ndarray:
    """850 nm IR frame: monochrome, retro-reflective plate background blows out, halo around it."""
    g = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY).astype(np.float32)
    g = np.clip((g - 90) * rng.uniform(1.3, 2.4) + 140, 0, 255)
    halo = cv2.GaussianBlur(g, (0, 0), max(1.0, g.shape[1] * 0.02))
    g = np.clip(g * 0.85 + halo * rng.uniform(0.1, 0.45), 0, 255)
    return cv2.cvtColor(g.astype(np.uint8), cv2.COLOR_GRAY2RGB)


def camera_effects(img: np.ndarray, rng: random.Random, min_w: int = 34, max_w: int = 220,
                   target_w: Optional[int] = None) -> np.ndarray:
    """Resolution loss, blur, sensor noise and compression."""
    h, w = img.shape[:2]
    target_w = target_w or rng.randint(min_w, max_w)
    s = target_w / w
    small = cv2.resize(img, (max(8, target_w), max(6, int(h * s))), interpolation=cv2.INTER_AREA)
    if rng.random() < 0.3:  # motion blur: 10-20 km/h at a 1/1000 s shutter smears a few pixels at most
        kmax = max(3, (target_w // 30) | 1)
        k = rng.choice([k for k in (3, 3, 3, 5, 5, 7) if k <= kmax] or [3])
        small = cv2.filter2D(small, -1, _motion_kernel(k, rng.uniform(-100, -80) if rng.random() < 0.6 else rng.uniform(-20, 20)))
    if rng.random() < 0.35:
        small = cv2.GaussianBlur(small, (0, 0), rng.uniform(0.3, 1.0))
    if rng.random() < 0.6:
        n = np.random.default_rng(rng.randint(0, 1 << 30)).normal(0, rng.uniform(2, 10), small.shape)
        small = np.clip(small.astype(np.float32) + n, 0, 255).astype(np.uint8)
    if rng.random() < 0.8:
        ok, enc = cv2.imencode(".jpg", small[..., ::-1], [cv2.IMWRITE_JPEG_QUALITY, rng.randint(25, 90)])
        small = cv2.imdecode(enc, cv2.IMREAD_COLOR)[..., ::-1]
    return small


def perspective(img: np.ndarray, rng: random.Random, strength: float = 1.0, pad_frac: float = 0.25,
                bg: Optional[np.ndarray] = None) -> tuple[np.ndarray, np.ndarray]:
    """Warp the plate as seen from a camera 2.5-3 m high, 5-7 m behind, slightly off-axis.
    Returns the warped image (with surrounding background) and the plate's 4 corners in it."""
    h, w = img.shape[:2]
    pw, ph = int(w * pad_frac), int(h * pad_frac)
    W, H = w + 2 * pw, h + 2 * ph
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    j = lambda a: rng.uniform(-a, a) * strength  # noqa: E731
    top_shrink = rng.uniform(0, 0.12) * strength * w  # camera above: top edge further away
    skew = j(0.12) * w
    dst = np.float32([
        [pw + top_shrink + skew + j(0.03) * w, ph + j(0.06) * h],
        [pw + w - top_shrink + skew + j(0.03) * w, ph + j(0.06) * h],
        [pw + w + j(0.03) * w, ph + h + j(0.06) * h],
        [pw + j(0.03) * w, ph + h + j(0.06) * h],
    ])
    ang = np.deg2rad(j(7))
    c = np.float32([W / 2, H / 2])
    rot = np.float32([[np.cos(ang), -np.sin(ang)], [np.sin(ang), np.cos(ang)]])
    dst = (dst - c) @ rot.T + c
    M = cv2.getPerspectiveTransform(src, dst)
    if bg is None:
        bg = random_background(rng, H, W)
    else:
        bg = cv2.resize(bg, (W, H))
    warped = cv2.warpPerspective(img, M, (W, H), borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    mask = cv2.warpPerspective(np.full((h, w), 255, np.uint8), M, (W, H))
    m = (cv2.GaussianBlur(mask, (3, 3), 0).astype(np.float32) / 255.0)[..., None]
    out = (warped.astype(np.float32) * m + bg.astype(np.float32) * (1 - m)).astype(np.uint8)
    return out, dst


def random_background(rng: random.Random, h: int, w: int) -> np.ndarray:
    """Clutter resembling a bike's rear: body panels, tail light, mudguard, shadows, stray text."""
    nrng = np.random.default_rng(rng.randint(0, 1 << 30))
    base = np.array([rng.randint(0, 255) for _ in range(3)], np.float32)
    img = np.ones((h, w, 3), np.float32) * base * rng.uniform(0.2, 1.0)
    # gradients and noise texture
    gy = np.linspace(rng.uniform(0.6, 1.3), rng.uniform(0.6, 1.3), h, dtype=np.float32)[:, None, None]
    img = img * gy
    tex = cv2.resize(nrng.random((max(2, h // 6), max(2, w // 6), 3)).astype(np.float32), (w, h)) * rng.uniform(10, 70)
    img = img + tex
    for _ in range(rng.randint(1, 7)):
        col = [rng.randint(0, 255) for _ in range(3)]
        if rng.random() < 0.15:
            col = [rng.randint(150, 255), rng.randint(0, 60), rng.randint(0, 60)]  # tail light
        x1, y1 = rng.randint(-w // 2, w), rng.randint(-h // 2, h)
        x2, y2 = x1 + rng.randint(w // 8, w), y1 + rng.randint(h // 8, h)
        if rng.random() < 0.5:
            cv2.rectangle(img, (x1, y1), (x2, y2), col, -1)
        else:
            cv2.ellipse(img, ((x1 + x2) // 2, (y1 + y2) // 2), (max(1, (x2 - x1) // 2), max(1, (y2 - y1) // 2)),
                        rng.uniform(0, 180), 0, 360, col, -1)
    img = np.clip(img, 0, 255).astype(np.uint8)
    if rng.random() < 0.3:  # stray text (stickers, brand names) that must not be read as a plate
        pil = Image.fromarray(img)
        f = _font(rng.choice(available_fonts())[0], max(8, int(h * rng.uniform(0.06, 0.2))))
        word = "".join(rng.choice("ABCDEFGHKLMNPRSTUVXYZ ") for _ in range(rng.randint(3, 9)))
        ImageDraw.Draw(pil).text((rng.randint(0, max(1, w // 2)), rng.randint(0, max(1, h - 10))), word, font=f,
                                 fill=tuple(rng.randint(0, 255) for _ in range(3)))
        img = np.array(pil)
    return img


# ----------------------------------------------------------------------------- OCR samples
OCR_H, OCR_W = 64, 128  # model input (grayscale); must match anpr_service.recognizers.trained


def ocr_sample(rng: random.Random, hard: float = 1.0) -> tuple[np.ndarray, str, dict]:
    """A plate crop as the plate finder would hand it to the reader: loosely cropped, degraded,
    resized to OCR_H x OCR_W grayscale."""
    plate, text, meta = render_plate(rng)
    # the camera sees the plate 30-230 px wide: do the (costly) effects near that resolution
    target_w = int(48 + (240 - 48) * rng.random() ** 1.2)
    work_w = int(np.clip(target_w * 1.8, 120, 360))
    plate = cv2.resize(plate, (work_w, max(8, int(plate.shape[0] * work_w / plate.shape[1]))), interpolation=cv2.INTER_AREA)
    rgb = dirt(plate, rng, rng.random() * hard if rng.random() < 0.6 else 0.0)
    if rng.random() < 0.3 * hard:  # faded paint: low text contrast
        rgb = cv2.addWeighted(rgb, rng.uniform(0.35, 0.8), np.full_like(rgb, int(np.mean(rgb))), 0.5, 0)
    warped, corners = perspective(rgb, rng, strength=rng.uniform(0.2, 1.0) * hard, pad_frac=0.18)
    # crop around the plate the way a detector box would: loose, tight or slightly clipped
    x1, y1 = corners[:, 0].min(), corners[:, 1].min()
    x2, y2 = corners[:, 0].max(), corners[:, 1].max()
    bw, bh = x2 - x1, y2 - y1
    lx = rng.uniform(-0.04, 0.12) * bw
    ly = rng.uniform(-0.05, 0.15) * bh
    cx1, cy1 = int(max(0, x1 - lx * rng.uniform(0.5, 1.5))), int(max(0, y1 - ly * rng.uniform(0.5, 1.5)))
    cx2 = int(min(warped.shape[1], x2 + lx * rng.uniform(0.5, 1.5)))
    cy2 = int(min(warped.shape[0], y2 + ly * rng.uniform(0.5, 1.5)))
    crop = warped[cy1:cy2, cx1:cx2]
    crop = lighting(crop, rng)
    night = rng.random() < 0.3
    if night:
        crop = ir_night(crop, rng)
    crop = camera_effects(crop, rng, target_w=int(target_w * crop.shape[1] / max(1.0, bw)))
    if rng.random() < 0.08 * hard:  # partial occlusion at an edge (rider's foot, bag strap)
        h, w = crop.shape[:2]
        side = rng.choice(["l", "r"])
        cw = int(w * rng.uniform(0.03, 0.1))
        col = [rng.randint(0, 80)] * 3
        if side == "l":
            crop[:, :cw] = col
        else:
            crop[:, w - cw:] = col
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    x = cv2.resize(gray, (OCR_W, OCR_H), interpolation=cv2.INTER_LINEAR)
    meta["night"] = night
    return x, text, meta


def preview(n: int = 24, seed: int = 0, path: str = "ocr_samples.png") -> str:
    rng = random.Random(seed)
    tiles = []
    for _ in range(n):
        x, t, m = ocr_sample(rng)
        tile = cv2.cvtColor(x, cv2.COLOR_GRAY2BGR)
        tile = cv2.copyMakeBorder(tile, 0, 18, 0, 0, cv2.BORDER_CONSTANT, value=(255, 255, 255))
        cv2.putText(tile, t, (2, OCR_H + 13), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 0, 180), 1)
        tiles.append(tile)
    cols = 6
    rows = [np.hstack(tiles[i:i + cols]) for i in range(0, n, cols)]
    cv2.imwrite(path, np.vstack(rows))
    return path


# ----------------------------------------------------------------------------- plate-finder scenes
DET_SIZE = 256  # training crop (the finder is fully convolutional; any size at inference)
_DECOY_WORDS = ["HONDA", "HERO", "TVS", "BAJAJ", "YAMAHA", "SUZUKI", "ACTIVA", "SPLENDOR", "PULSAR", "JUPITER",
                "ROYAL ENFIELD", "KTM", "OLA", "ATHER", "ACCESS", "DIO", "SHINE", "APACHE", "PLATINA", "MAESTRO",
                "PARKING", "EXIT", "ENTRY", "NO PARKING", "SLOW", "STATION ROAD", "PRESS", "POLICE"]


def _decoys(img: np.ndarray, rng: random.Random) -> None:
    """Things that look a bit like plates but are not: stickers, brand badges, blank boards, reflectors."""
    h, w = img.shape[:2]
    fonts = available_fonts()
    for _ in range(rng.randint(0, 4)):
        kind = rng.random()
        x, y = rng.randint(0, w - 10), rng.randint(0, h - 10)
        if kind < 0.45:  # text badge / sticker
            f = _font(rng.choice(fonts)[0], rng.randint(8, 26))
            pil = Image.fromarray(img)
            d = ImageDraw.Draw(pil)
            word = rng.choice(_DECOY_WORDS)
            if rng.random() < 0.5:
                l, t, r, b = d.textbbox((x, y), word, font=f)
                d.rectangle((l - 3, t - 3, r + 3, b + 3), fill=tuple(rng.randint(0, 255) for _ in range(3)))
            d.text((x, y), word, font=f, fill=tuple(rng.randint(0, 255) for _ in range(3)))
            img[:] = np.array(pil)
        elif kind < 0.7:  # blank light rectangle (reflector board, sticker without text)
            bw, bh = rng.randint(15, 90), rng.randint(8, 45)
            cv2.rectangle(img, (x, y), (x + bw, y + bh), (rng.randint(170, 255),) * 3, -1)
        elif kind < 0.85:  # tail light / indicator
            cv2.ellipse(img, (x, y), (rng.randint(5, 25), rng.randint(3, 12)), 0, 0, 360,
                        (rng.randint(180, 255), rng.randint(0, 80), rng.randint(0, 40)), -1)
        else:  # stripes (railing, zebra marking)
            for k in range(rng.randint(2, 6)):
                yy = y + k * rng.randint(6, 14)
                cv2.line(img, (x, yy), (min(w - 1, x + rng.randint(30, 150)), yy), (rng.randint(150, 255),) * 3, rng.randint(1, 4))


def detector_scene(rng: random.Random, size: int = DET_SIZE) -> tuple[np.ndarray, np.ndarray]:
    """A grayscale gate-camera crop with 0-3 plates. Returns (image uint8 HxW, boxes float32 [N,4] x1y1x2y2)."""
    img = random_background(rng, size, size)
    _decoys(img, rng)
    boxes = []
    n = rng.choices([0, 1, 1, 1, 2, 2, 3], k=1)[0]
    for _ in range(n):
        plate, _text, _meta = render_plate(rng)
        target_w = int(18 + (130 - 18) * rng.random() ** 1.1)
        ph = max(6, int(plate.shape[0] * target_w / plate.shape[1]))
        plate = cv2.resize(plate, (target_w, ph), interpolation=cv2.INTER_AREA)
        plate = dirt(plate, rng, rng.random() * 0.8 if rng.random() < 0.5 else 0.0)
        # paste with perspective: warp onto a patch of the scene itself
        pad = 0.3
        pw_, ph_ = int(target_w * (1 + 2 * pad)), int(ph * (1 + 2 * pad))
        if pw_ >= size or ph_ >= size:
            continue
        x0, y0 = rng.randint(0, size - pw_), rng.randint(0, size - ph_)
        if rng.random() < 0.65:  # the bike it is mounted on: dark mudguard behind, tail light above
            cx, cy = x0 + pw_ // 2, y0 + ph_ // 2
            body = (rng.randint(0, 70),) * 3 if rng.random() < 0.7 else tuple(rng.randint(0, 255) for _ in range(3))
            cv2.ellipse(img, (cx, cy + ph_ // 4), (int(pw_ * rng.uniform(0.6, 0.9)), int(ph_ * rng.uniform(0.9, 1.6))),
                        0, 0, 360, body, -1)
            if rng.random() < 0.7:
                cv2.ellipse(img, (cx, cy - int(ph_ * rng.uniform(0.7, 1.1))), (max(3, pw_ // 4), max(2, ph_ // 7)), 0, 0, 360,
                            (rng.randint(180, 255), rng.randint(0, 70), rng.randint(0, 40)), -1)
        patch = img[y0:y0 + ph_, x0:x0 + pw_]
        warped, corners = perspective(plate, rng, strength=rng.uniform(0.2, 1.0), pad_frac=pad, bg=patch)
        warped = cv2.resize(warped, (pw_, ph_))
        sx, sy = pw_ / (target_w * (1 + 2 * pad)), ph_ / (ph * (1 + 2 * pad))
        corners = corners * np.float32([sx, sy])
        bx1, by1 = corners[:, 0].min() + x0, corners[:, 1].min() + y0
        bx2, by2 = corners[:, 0].max() + x0, corners[:, 1].max() + y0
        # skip if it overlaps an earlier plate a lot (side-by-side bikes do not stack plates)
        if any(not (bx2 < b[0] or bx1 > b[2] or by2 < b[1] or by1 > b[3]) for b in boxes):
            continue
        img[y0:y0 + ph_, x0:x0 + pw_] = warped
        boxes.append([bx1, by1, bx2, by2])
    img = lighting(img, rng)
    if rng.random() < 0.3:
        img = ir_night(img, rng)
    img = camera_effects(img, rng, target_w=size) if rng.random() < 0.9 else img
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    return gray, np.array(boxes, np.float32).reshape(-1, 4)


def preview_scenes(n: int = 12, seed: int = 0, path: str = "scene_samples.png") -> str:
    rng = random.Random(seed)
    tiles = []
    for _ in range(n):
        g, boxes = detector_scene(rng)
        t = cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)
        for b in boxes.astype(int):
            cv2.rectangle(t, (b[0], b[1]), (b[2], b[3]), (0, 200, 0), 1)
        tiles.append(cv2.copyMakeBorder(t, 2, 2, 2, 2, cv2.BORDER_CONSTANT, value=(255, 255, 255)))
    cols = 4
    cv2.imwrite(path, np.vstack([np.hstack(tiles[i:i + cols]) for i in range(0, n, cols)]))
    return path


if __name__ == "__main__":
    print(preview())
    print(preview_scenes())
