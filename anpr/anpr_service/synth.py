"""Synthetic replay-video generator (rear-view gate scenes with ground truth).

Renders what the two rear-facing ANPR cameras (and the overview camera) of a
gate see: a road with painted guide markings and a rumble strip, and
two-wheelers drawn as simple shapes (wheel, body, rider, helmet, tail lamp)
carrying single-line (``MH 43 AB 1234``) or two-line (``MH 43`` over
``AB 1234``) plates rendered with Hershey fonts.  The scene uses a simple
pinhole-like projection so vehicles shrink as they ride away, side-by-side
pairs and staggered overlapping riders occur, and the LEFT/RIGHT cameras
overlap by ~30% so a bike in the middle is seen by both.

A ``ground_truth.json`` sidecar (plates, crossing times, lateral position,
which cameras can read the plate) is written next to the videos, plus a
ready-to-run ``site.synth.yaml`` for ``python -m anpr_service replay``.
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import yaml

from .plates import format_display, split_rows

log = logging.getLogger(__name__)

GATE_WIDTH_M = 3.6
Z_LINE = 8.0  # capture line distance from the cameras (m)
_A, _B = -0.013, 5.067  # ground row: y = H * (A + B / z)
VERT = 0.55  # vertical foreshortening of the tilted camera
PLATE_HEIGHT_M = 0.66

CAPTURE_LINE_Y = _A + _B / Z_LINE  # as a fraction of frame height (~0.62)

SPANS = {"L": (0.0, 0.65), "R": (0.35, 1.0), "O": (-0.1, 1.1)}


@dataclass
class SynthVehicle:
    plate: str
    layout: str = "two_line"  # "two_line" | "single"
    lateral: float = 0.5  # 0 = left edge of the gate, 1 = right edge
    start_s: float = 0.5
    speed_mps: float = 3.5
    z_start: float = 4.3
    direction: int = 1  # +1 riding away from the cameras (IN), -1 towards them
    font: int = cv2.FONT_HERSHEY_SIMPLEX
    body_color: tuple[int, int, int] = (40, 40, 160)
    rider_color: tuple[int, int, int] = (120, 60, 30)
    helmet_color: tuple[int, int, int] = (20, 20, 20)
    unreadable: bool = False
    side_by_side: bool = False
    occluded: bool = False
    vid: str = ""

    def z_at(self, t: float) -> float:
        return self.z_start + (t - self.start_s) * self.speed_mps * self.direction

    @property
    def cross_s(self) -> float:
        return self.start_s + (Z_LINE - self.z_start) / (self.speed_mps * self.direction)


@dataclass
class SynthCamera:
    id: str
    role: str  # ANPR | OVERVIEW
    span: tuple[float, float]
    file: str


@dataclass
class SynthScenario:
    gate_id: str = "G1"
    direction: str = "IN"
    width: int = 1280
    height: int = 720
    fps: float = 25.0
    duration_s: float = 14.0
    vehicles: list[SynthVehicle] = field(default_factory=list)
    cameras: list[SynthCamera] = field(default_factory=list)
    seed: int = 7
    noise_sigma: float = 2.0


# ----------------------------------------------------------------- projection
def ground_y(z: float, h: int) -> float:
    return h * (_A + _B / z)


def ppm(z: float, w: int, span: tuple[float, float]) -> float:
    return w / ((span[1] - span[0]) * GATE_WIDTH_M) * Z_LINE / z


def x_img(x_m: float, z: float, w: int, span: tuple[float, float]) -> float:
    xc = (span[0] + span[1]) / 2 * GATE_WIDTH_M
    return w / 2 + (x_m - xc) * ppm(z, w, span)


# --------------------------------------------------------------------- plates
def render_plate(plate: str, layout: str, font: int = cv2.FONT_HERSHEY_SIMPLEX, px_per_m: float = 2000.0,
                 unreadable: bool = False, rng: random.Random | None = None,
                 background: tuple[int, int, int] = (236, 236, 236)) -> np.ndarray:
    """Render a high-resolution plate image (BGR)."""
    if layout == "two_line":
        pw, ph = 0.20, 0.10
    else:
        pw, ph = 0.285, 0.05
    w, h = int(pw * px_per_m), int(ph * px_per_m)
    img = np.full((h, w, 3), background, dtype=np.uint8)
    b = max(2, int(0.035 * h))
    cv2.rectangle(img, (b, b), (w - 1 - b, h - 1 - b), (30, 30, 30), max(2, b // 2))
    ink = (22, 22, 22)
    if layout == "two_line":
        r1, r2 = split_rows(plate)
        rows = [format_display(r1) if len(r1) > 3 else r1, _space_row2(r2)]
        text_h = int(h * 0.30)
        centres = [0.30, 0.72]
    else:
        rows = [format_display(plate)]
        text_h = int(h * 0.58)
        centres = [0.52]
    thickness = max(2, int(text_h * 0.12))
    for text, cy in zip(rows, centres):
        scale = cv2.getFontScaleFromHeight(font, text_h, thickness)
        (tw, th), _ = cv2.getTextSize(text, font, scale, thickness)
        max_w = int(w * 0.88)
        if tw > max_w:
            scale *= max_w / tw
            (tw, th), _ = cv2.getTextSize(text, font, scale, thickness)
        org = (int((w - tw) / 2), int(cy * h + th / 2))
        cv2.putText(img, text, org, font, scale, ink, thickness, cv2.LINE_AA)
    if unreadable:
        rng = rng or random.Random(0)
        for _ in range(40):
            cx, cy = rng.randint(0, w), rng.randint(0, h)
            ax, ay = rng.randint(w // 14, w // 5), rng.randint(h // 10, h // 3)
            col = (rng.randint(40, 70), rng.randint(60, 90), rng.randint(80, 110))
            cv2.ellipse(img, (cx, cy), (ax, ay), rng.randint(0, 180), 0, 360, col, -1)
        img = cv2.GaussianBlur(img, (0, 0), h / 25)
    return img


def _space_row2(r2: str) -> str:
    # "AB1234" -> "AB 1234", "1234AA" -> "1234 AA"
    if r2[:1].isalpha():
        i = next((k for k, c in enumerate(r2) if c.isdigit()), len(r2))
        return (r2[:i] + " " + r2[i:]).strip()
    i = next((k for k, c in enumerate(r2) if c.isalpha()), len(r2))
    return (r2[:i] + " " + r2[i:]).strip()


# ---------------------------------------------------------------- background
def render_background(w: int, h: int, span: tuple[float, float], seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    ys = np.arange(h, dtype=np.float64)[:, None]
    xs = np.arange(w, dtype=np.float64)[None, :]
    denom = ys / h - _A
    with np.errstate(divide="ignore", invalid="ignore"):
        z = np.where(denom > 0, _B / denom, np.inf)
    z = np.broadcast_to(z, (h, w))
    far = z > 40
    xc = (span[0] + span[1]) / 2 * GATE_WIDTH_M
    scale = w / ((span[1] - span[0]) * GATE_WIDTH_M) * Z_LINE
    with np.errstate(invalid="ignore"):
        x_m = xc + (xs - w / 2) * np.where(np.isfinite(z), z, 40) / scale
    img = np.full((h, w), 96, dtype=np.float32)
    img += rng.normal(0, 7, (h, w)).astype(np.float32)
    img = cv2.GaussianBlur(img, (0, 0), 1.2)
    mark = 150.0
    # Gate edges (solid) and centre guide (dashed every 1 m).
    for edge in (0.05, GATE_WIDTH_M - 0.05):
        img[(np.abs(x_m - edge) < 0.05) & ~far] = mark
    dashed = (np.abs(x_m - GATE_WIDTH_M / 2) < 0.04) & ((np.floor(z) % 2) == 0) & ~far
    img[dashed] = mark
    # Rumble strip before the capture line.
    strip = (z > 6.3) & (z < 6.6) & ~far
    bars = (np.floor(x_m / 0.15) % 2) == 0
    img[strip & bars] = 55
    img[strip & ~bars] = 125
    img[far] = 70
    img = np.clip(img, 0, 255).astype(np.uint8)
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)


# ------------------------------------------------------------------- vehicles
def _paste(dst: np.ndarray, src: np.ndarray, x0: int, y0: int) -> None:
    h, w = src.shape[:2]
    H, W = dst.shape[:2]
    xa, ya = max(0, x0), max(0, y0)
    xb, yb = min(W, x0 + w), min(H, y0 + h)
    if xb <= xa or yb <= ya:
        return
    dst[ya:yb, xa:xb] = src[ya - y0 : yb - y0, xa - x0 : xb - x0]


def draw_vehicle(img: np.ndarray, v: SynthVehicle, z: float, span: tuple[float, float],
                 plate_img: np.ndarray) -> None:
    h, w = img.shape[:2]
    s = ppm(z, w, span)
    sv = VERT * s
    cx = x_img(v.lateral * GATE_WIDTH_M, z, w, span)
    gy = ground_y(z, h)

    def pt(dx: float, hh: float) -> tuple[int, int]:
        return int(round(cx + dx * s)), int(round(gy - hh * sv))

    # rear wheel
    cv2.rectangle(img, pt(-0.055, 0.55), pt(0.055, 0.0), (25, 25, 25), -1)
    # body / mudguard
    body = np.array([pt(-0.14, 0.40), pt(0.14, 0.40), pt(0.20, 0.97), pt(-0.20, 0.97)], dtype=np.int32)
    cv2.fillConvexPoly(img, body, v.body_color)
    # rider
    cv2.rectangle(img, pt(-0.24, 1.55), pt(0.24, 0.95), v.rider_color, -1)
    head_c = pt(0.0, 1.70)
    cv2.circle(img, head_c, max(2, int(0.13 * s)), v.helmet_color, -1)
    # tail lamp
    cv2.rectangle(img, pt(-0.09, 0.99), pt(0.09, 0.93), (20, 20, 210), -1)
    # plate (true aspect, not foreshortened)
    ph_m = 0.10 if v.layout == "two_line" else 0.05
    pw_m = 0.20 if v.layout == "two_line" else 0.285
    pw_px, ph_px = int(round(pw_m * s)), int(round(ph_m * s))
    if pw_px < 6 or ph_px < 3:
        return
    small = cv2.resize(plate_img, (pw_px, ph_px), interpolation=cv2.INTER_AREA)
    px0 = int(round(cx - pw_px / 2))
    py0 = int(round(gy - PLATE_HEIGHT_M * sv - ph_px / 2))
    _paste(img, small, px0, py0)


def plate_u_range(v: SynthVehicle) -> tuple[float, float]:
    pw_m = 0.20 if v.layout == "two_line" else 0.285
    half = pw_m / 2 / GATE_WIDTH_M
    return v.lateral - half, v.lateral + half


def plate_visible_at_line(v: SynthVehicle, span: tuple[float, float], margin: float = 0.01) -> bool:
    u0, u1 = plate_u_range(v)
    return u0 >= span[0] + margin and u1 <= span[1] - margin


# ------------------------------------------------------------------ scenarios
FONTS = (cv2.FONT_HERSHEY_SIMPLEX, cv2.FONT_HERSHEY_DUPLEX)
COLORS = [
    ((40, 40, 160), (120, 60, 30), (20, 20, 20)),
    ((150, 60, 20), (40, 90, 40), (30, 30, 140)),
    ((30, 110, 30), (90, 40, 110), (140, 40, 30)),
    ((20, 80, 140), (60, 60, 60), (40, 120, 20)),
    ((110, 30, 110), (30, 70, 120), (25, 25, 25)),
]


def default_vehicles(compact: bool = False, seed: int = 7) -> list[SynthVehicle]:
    """The reference scene: side-by-side pair, a middle bike seen by both
    cameras, a staggered/partly occluded pair, a BH-series plate and an
    unreadable plate."""
    rnd = random.Random(seed)

    def colours(i: int) -> dict[str, Any]:
        b, r, hcol = COLORS[i % len(COLORS)]
        return {"body_color": b, "rider_color": r, "helmet_color": hcol}

    vs = [
        SynthVehicle("MH43AB1234", "single", 0.20, 0.6, 3.4, side_by_side=True, font=FONTS[0], **colours(0)),
        SynthVehicle("MH12DE4521", "two_line", 0.80, 0.6, 3.4, side_by_side=True, font=FONTS[1], **colours(1)),
        SynthVehicle("MH14GX0786", "two_line", 0.50, 2.9, 3.6, font=FONTS[0], **colours(2)),
        SynthVehicle("KA05MN2468", "two_line", 0.24, 5.0, 3.3, z_start=4.3, side_by_side=True, font=FONTS[1],
                     **colours(3)),
        SynthVehicle("DL3CAF5031", "two_line", 0.37, 5.0, 3.3, z_start=5.3, side_by_side=True, occluded=True,
                     font=FONTS[0], **colours(4)),
    ]
    if not compact:
        vs += [
            SynthVehicle("22BH1234AA", "two_line", 0.47, 7.6, 3.5, font=FONTS[1], **colours(0)),
            SynthVehicle("GJ01KP7730", "single", 0.66, 9.6, 3.2, font=FONTS[0], **colours(1)),
            SynthVehicle("TN09BZ0005", "two_line", 0.33, 9.6, 3.2, side_by_side=True, font=FONTS[1],
                         **colours(2)),
            SynthVehicle("MH04XY0000", "two_line", 0.75, 11.8, 3.5, unreadable=True, **colours(3)),
        ]
        vs[6].side_by_side = True
    else:
        vs.append(SynthVehicle("MH04XY0000", "two_line", 0.78, 7.4, 3.5, unreadable=True, **colours(3)))
    for i, v in enumerate(vs, 1):
        v.vid = f"V{i}"
        v.speed_mps += rnd.uniform(-0.05, 0.05)
    return vs


def default_scenario(gate_id: str = "G1", width: int = 1280, height: int = 720, fps: float = 25.0,
                     compact: bool = False, overview: bool = True, ext: str = ".mp4",
                     seed: int = 7) -> SynthScenario:
    vehicles = default_vehicles(compact=compact, seed=seed)
    duration = max(v.cross_s for v in vehicles) + 2.0
    cams = [
        SynthCamera(f"{gate_id}-L", "ANPR", SPANS["L"], f"{gate_id}-L{ext}"),
        SynthCamera(f"{gate_id}-R", "ANPR", SPANS["R"], f"{gate_id}-R{ext}"),
    ]
    if overview:
        cams.append(SynthCamera(f"{gate_id}-O", "OVERVIEW", SPANS["O"], f"{gate_id}-O{ext}"))
    return SynthScenario(gate_id=gate_id, width=width, height=height, fps=fps, duration_s=round(duration, 2),
                         vehicles=vehicles, cameras=cams, seed=seed)


def _fourcc_for(path: Path) -> int:
    if path.suffix.lower() == ".avi":
        return cv2.VideoWriter_fourcc(*"MJPG")
    return cv2.VideoWriter_fourcc(*"mp4v")


def render_scenario(sc: SynthScenario, out_dir: str | Path) -> dict[str, Any]:
    """Render all camera videos of a scenario and write ``ground_truth.json``."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(sc.seed)
    plates = {v.vid: render_plate(v.plate, v.layout, v.font, unreadable=v.unreadable, rng=rng) for v in sc.vehicles}
    writers: dict[str, cv2.VideoWriter] = {}
    backgrounds: dict[str, np.ndarray] = {}
    for i, cam in enumerate(sc.cameras):
        path = out / cam.file
        wr = cv2.VideoWriter(str(path), _fourcc_for(path), sc.fps, (sc.width, sc.height))
        if not wr.isOpened():
            raise RuntimeError(f"cannot open video writer for {path}")
        writers[cam.id] = wr
        backgrounds[cam.id] = render_background(sc.width, sc.height, cam.span, sc.seed + i)
    nrng = np.random.default_rng(sc.seed)
    noise_bank = [
        nrng.normal(0, sc.noise_sigma, (sc.height, sc.width, 1)).astype(np.int16) for _ in range(6)
    ]
    n_frames = int(round(sc.duration_s * sc.fps))
    for fi in range(n_frames):
        t = fi / sc.fps
        active = []
        for v in sc.vehicles:
            z = v.z_at(t)
            if t >= v.start_s and 3.5 < z < 30:
                active.append((z, v))
        active.sort(key=lambda zv: -zv[0])  # far first (painter's algorithm)
        noise = noise_bank[fi % len(noise_bank)]
        for cam in sc.cameras:
            frame = backgrounds[cam.id].copy()
            for z, v in active:
                draw_vehicle(frame, v, z, cam.span, plates[v.vid])
            if sc.noise_sigma > 0:
                frame = np.clip(frame.astype(np.int16) + noise, 0, 255).astype(np.uint8)
            writers[cam.id].write(frame)
    for wr in writers.values():
        wr.release()

    gt = build_ground_truth(sc)
    (out / "ground_truth.json").write_text(json.dumps(gt, indent=2), encoding="utf-8")
    log.info("rendered %d frames x %d cameras for gate %s into %s", n_frames, len(sc.cameras), sc.gate_id, out)
    return gt


def build_ground_truth(sc: SynthScenario) -> dict[str, Any]:
    vehicles = []
    for v in sc.vehicles:
        visible = [c.id for c in sc.cameras if c.role == "ANPR" and plate_visible_at_line(v, c.span)]
        vehicles.append({
            "id": v.vid,
            "plate": v.plate,
            "layout": v.layout,
            "lateral": round(v.lateral, 4),
            "cross_ms": int(round(v.cross_s * 1000)),
            "direction": "IN" if v.direction > 0 else "OUT",
            "visible_in": visible,
            "side_by_side": v.side_by_side,
            "occluded": v.occluded,
            "unreadable": v.unreadable,
        })
    return {
        "gate_id": sc.gate_id,
        "direction": sc.direction,
        "fps": sc.fps,
        "width": sc.width,
        "height": sc.height,
        "duration_s": sc.duration_s,
        "capture_line": [[0.0, round(CAPTURE_LINE_Y, 4)], [1.0, round(CAPTURE_LINE_Y, 4)]],
        "roi": [[0.0, 0.12], [1.0, 0.12], [1.0, 1.0], [0.0, 1.0]],
        "in_vector": [0.0, -1.0],
        "cameras": [
            {"id": c.id, "role": c.role, "file": c.file, "gate_span": [max(0.0, c.span[0]), min(1.0, c.span[1])]}
            for c in sc.cameras
        ],
        "vehicles": vehicles,
    }


def camera_entries(gt: dict[str, Any], base_dir: Path) -> list[dict[str, Any]]:
    """Camera config entries for a rendered scenario."""
    return [
        {
            "id": c["id"],
            "role": c["role"],
            "replay_file": str((base_dir / c["file"]).resolve()),
            "roi": gt["roi"],
            "capture_line": gt["capture_line"],
            "in_vector": gt["in_vector"],
            "gate_span": c["gate_span"],
        }
        for c in gt["cameras"]
    ]


def generate(out_dir: str | Path, gates: list[str] | None = None, width: int = 1280, height: int = 720,
             fps: float = 25.0, compact: bool = False, overview: bool = True, ext: str = ".mp4",
             seed: int = 7) -> Path:
    """Render one scenario per gate and write ``site.synth.yaml``.  Returns the config path."""
    out = Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    gate_entries = []
    for gi, gate_id in enumerate(gates or ["G1", "G2"]):
        sc = default_scenario(gate_id, width, height, fps, compact=compact, overview=overview, ext=ext,
                              seed=seed + gi)
        gdir = out / gate_id
        gt = render_scenario(sc, gdir)
        gate_entries.append({
            "id": gate_id,
            "name": f"Gate {gate_id[1:] if gate_id[1:].isdigit() else gate_id}",
            "direction": gt["direction"],
            "cameras": camera_entries(gt, gdir),
        })
    cfg = {
        "site_id": "synthetic-demo",
        "image_root": str(out / "images"),
        "outbox_path": str(out / "outbox.sqlite"),
        "backend": {"url": "${BACKEND_URL:-}", "api_key": "${ANPR_API_KEY:-}"},
        "recognizer": {"kind": "classical"},
        "emitter": {"events_jsonl": str(out / "events.jsonl")},
        "gates": gate_entries,
    }
    cfg_path = out / "site.synth.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    (out / "scenario.json").write_text(
        json.dumps({g: [asdict(v) for v in default_vehicles(compact, seed + i)] for i, g in enumerate(gates or ["G1", "G2"])},
                   indent=2, default=str),
        encoding="utf-8",
    )
    return cfg_path
