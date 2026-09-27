"""Service configuration (YAML + optional refresh from the backend).

The YAML file is the source of truth for things only the edge box knows
(recogniser, model paths, replay files, image root...).  Gate direction,
camera URLs/ROIs and merge/dedupe settings can be overridden at runtime by
``GET {backend}/api/anpr/config`` (see :func:`apply_remote_config`).

``${VAR}`` and ``${VAR:-default}`` are expanded from the environment in every
string value of the YAML.
"""

from __future__ import annotations

import copy
import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .plates import DEFAULT_STATE_CODES, PlateRules
from .types import CameraRole, CameraSide, GateDirection

_CRED_RE = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)(?P<user>[^:@/\s]+):(?P<pw>[^@/\s]+)@", re.I)


def redact_url(url: str | None) -> str:
    """Hide the password in ``rtsp://user:pass@host/...`` for logs and CLI output."""
    if not url:
        return ""
    return _CRED_RE.sub(lambda m: f"{m.group('scheme')}{m.group('user')}:***@", url)


_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), value)
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    return value


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", use_enum_values=False)


class BackendConfig(_Model):
    url: str = ""  # empty -> no backend (events go to the outbox / JSONL sink only)
    api_key: str = ""
    config_refresh_s: float = 30.0
    timeout_s: float = 3.0
    events_path: str = "/api/anpr/events"
    heartbeat_path: str = "/api/devices/heartbeat"
    config_path: str = "/api/anpr/config"
    # Who owns camera geometry (roi, capture_line, in_vector)?
    #   prefer_local   - YAML geometry wins whenever the YAML camera has a capture_line;
    #                    the backend's is used only for cameras without local geometry.
    #   prefer_backend - non-empty backend geometry wins (geometry edited in the admin UI).
    # Gate direction and merge/dedupe settings ALWAYS come from the backend when it answers.
    geometry: Literal["prefer_local", "prefer_backend"] = "prefer_local"


class TrackerConfig(_Model):
    iou_threshold: float = 0.2
    max_center_distance: float = 0.6
    max_age: int = 12
    min_hits: int = 2


class PipelineConfig(_Model):
    process_every_n: int = 1  # analyse every n-th frame (commercial API: raise this)
    track_anchor: Literal["bottom", "center"] = "bottom"
    min_plate_width_px: int = 36
    min_sharpness: float = 12.0  # variance of Laplacian of the plate crop
    max_plate_occlusion: float = 0.2  # skip frames where a nearer vehicle covers > this of the plate
    min_votes: int = 3
    max_votes: int = 40
    post_cross_s: float = 0.25  # keep voting this long after the crossing unless already confident
    min_track_hits: int = 3  # ignore crossings of tracks seen in fewer frames (noise)
    jpeg_quality: int = 88
    full_frame_max_width: int = 1920
    annotate_full_frame: bool = True
    tracker: TrackerConfig = Field(default_factory=TrackerConfig)


class Settings(_Model):
    merge_window_s: float = 10.0
    dedupe_window_s: float = 60.0
    min_confidence: float = 0.6
    merge_hold_s: float = 0.5  # wait this long (event time) for the other camera of a crossing
    unread_merge_s: float = 1.5
    unread_merge_dx: float = 0.15  # lateral distance (fraction of gate width) for UNREAD merging
    heartbeat_s: float = 10.0
    camera_stale_s: float = 3.0  # a camera silent this long no longer holds back merging
    overview_snapshot_s: float = 0.25
    overview_buffer_s: float = 20.0


class ClassicalConfig(_Model):
    bg_max_width: int = 640
    bg_history: int = 300
    bg_var_threshold: float = 24.0
    bg_var_init: float = 36.0
    min_vehicle_area_frac: float = 0.004
    warmup_frames: int = 5
    plate_min_v: int = 170
    plate_max_s: int = 70
    plate_min_aspect: float = 1.2
    plate_max_aspect: float = 7.0
    ocr_row_height: int = 40
    car_min_aspect: float = 1.15  # blob width/height above this (and large) -> CAR


class OnnxConfig(_Model):
    detector_model: str = "/models/vehicle_plate_detector.onnx"
    detector_format: Literal["auto", "yolov8", "yolov5"] = "auto"
    detector_input_size: int = 640
    # class index -> BIKE | CAR | OTHER | PLATE
    detector_classes: dict[int, str] = Field(default_factory=lambda: {0: "BIKE", 1: "CAR", 2: "OTHER", 3: "PLATE"})
    plate_detector_model: str | None = None  # optional second-stage plate detector on vehicle crops
    plate_detector_input_size: int = 320
    conf_threshold: float = 0.35
    nms_iou: float = 0.5
    ocr_model: str = "/models/plate_ocr_crnn.onnx"
    ocr_alphabet: str = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    ocr_blank_index: int = 0
    ocr_input_height: int = 32
    ocr_input_width: int = 128
    ocr_channels: int = 1
    ocr_mean: float = 0.5
    ocr_std: float = 0.5
    providers: list[str] = Field(
        default_factory=lambda: ["TensorrtExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"]
    )


class CommercialConfig(_Model):
    api_url: str = "https://api.platerecognizer.com/v1/plate-reader/"
    api_key_env: str = "PLATE_RECOGNIZER_API_KEY"
    regions: list[str] = Field(default_factory=lambda: ["in"])
    mmc: bool = False
    timeout_s: float = 5.0
    min_interval_s: float = 0.0  # throttle per camera (0 = every analysed frame)


class TrainedConfig(_Model):
    """Our own plate models (trained on computer-generated plates, fine-tuned on site footage).

    Relative model paths resolve against the ``anpr/`` directory (``/app`` in the image), so the
    bundled ``models/`` work out of the box; point them elsewhere after a site fine-tune.
    """

    finder_model: str = "models/plate_finder.onnx"
    reader_model: str = "models/plate_reader.onnx"
    finder_width: int = 960  # frames are scaled to this width for the plate finder (multiple of 16)
    finder_threshold: float = 0.35
    whole_frame_plates: bool = True  # also report plates on vehicles the motion detector missed
    idle_scan_every: int = 5  # with no motion, scan the whole frame for plates every N frames
    threads: int = 1  # per camera process; cameras already run in parallel


class RecognizerConfig(_Model):
    kind: Literal["trained", "classical", "onnx", "commercial"] = "classical"
    trained: TrainedConfig = Field(default_factory=TrainedConfig)
    classical: ClassicalConfig = Field(default_factory=ClassicalConfig)
    onnx: OnnxConfig = Field(default_factory=OnnxConfig)
    commercial: CommercialConfig = Field(default_factory=CommercialConfig)


class PlateRulesConfig(_Model):
    state_codes: list[str] = Field(default_factory=lambda: list(DEFAULT_STATE_CODES))
    allow_bh: bool = True
    require_state_code: bool = True

    def to_rules(self) -> PlateRules:
        return PlateRules.from_config(self.state_codes, self.allow_bh, self.require_state_code)


class IngestConfig(_Model):
    api_preference: Literal["ffmpeg", "gstreamer", "any"] = "ffmpeg"
    rtsp_transport: Literal["tcp", "udp"] = "tcp"
    reconnect_initial_s: float = 1.0
    reconnect_max_s: float = 30.0
    read_timeout_s: float = 5.0
    queue_size: int = 50


class EmitterConfig(_Model):
    retry_initial_s: float = 1.0
    retry_max_s: float = 30.0
    events_jsonl: str | None = None  # also append every delivered event to this JSONL file


class CameraConfig(_Model):
    id: str
    role: CameraRole = CameraRole.ANPR
    side: CameraSide | None = None
    enabled: bool = True
    rtsp_url: str | None = None
    gstreamer_pipeline: str | None = None
    replay_file: str | None = None
    replay_loop: bool = False
    roi: list[list[float]] | None = None
    capture_line: list[list[float]] | None = None
    in_vector: list[float] = Field(default_factory=lambda: [0.0, -1.0])
    # Part of the gate width covered by this camera at the capture line,
    # as fractions (0 = left edge of the gate, 1 = right edge).
    gate_span: list[float] | None = None

    @field_validator("role", mode="before")
    @classmethod
    def _upper_role(cls, v: Any) -> Any:
        return v.upper() if isinstance(v, str) else v

    @field_validator("side", mode="before")
    @classmethod
    def _upper_side(cls, v: Any) -> Any:
        return v.upper() if isinstance(v, str) else v

    @property
    def effective_side(self) -> CameraSide:
        if self.side is not None:
            return self.side
        if self.role == CameraRole.OVERVIEW:
            return CameraSide.OVERVIEW
        suffix = self.id.upper().rsplit("-", 1)[-1]
        if suffix in ("L", "LEFT"):
            return CameraSide.LEFT
        if suffix in ("R", "RIGHT"):
            return CameraSide.RIGHT
        return CameraSide.CENTER

    @property
    def effective_gate_span(self) -> tuple[float, float]:
        if self.gate_span and len(self.gate_span) == 2:
            return float(self.gate_span[0]), float(self.gate_span[1])
        side = self.effective_side
        if side == CameraSide.LEFT:
            return 0.0, 0.65
        if side == CameraSide.RIGHT:
            return 0.35, 1.0
        return 0.0, 1.0


class GateConfig(_Model):
    id: str
    name: str = ""
    direction: GateDirection = GateDirection.BOTH
    cameras: list[CameraConfig] = Field(default_factory=list)

    @field_validator("direction", mode="before")
    @classmethod
    def _upper_dir(cls, v: Any) -> Any:
        return v.upper() if isinstance(v, str) else v

    def anpr_cameras(self) -> list[CameraConfig]:
        return [c for c in self.cameras if c.role == CameraRole.ANPR and c.enabled]

    def overview_cameras(self) -> list[CameraConfig]:
        return [c for c in self.cameras if c.role == CameraRole.OVERVIEW and c.enabled]


class ServiceConfig(_Model):
    site_id: str = "site"
    image_root: str = "/data/images"
    outbox_path: str = "/data/anpr/outbox.sqlite"
    log_level: str = "INFO"
    backend: BackendConfig = Field(default_factory=BackendConfig)
    recognizer: RecognizerConfig = Field(default_factory=RecognizerConfig)
    plates: PlateRulesConfig = Field(default_factory=PlateRulesConfig)
    settings: Settings = Field(default_factory=Settings)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    ingest: IngestConfig = Field(default_factory=IngestConfig)
    emitter: EmitterConfig = Field(default_factory=EmitterConfig)
    gates: list[GateConfig] = Field(default_factory=list)

    def gate(self, gate_id: str) -> GateConfig:
        for g in self.gates:
            if g.id == gate_id:
                return g
        raise KeyError(f"unknown gate {gate_id!r}")

    def camera(self, camera_id: str) -> tuple[GateConfig, CameraConfig]:
        for g in self.gates:
            for c in g.cameras:
                if c.id == camera_id:
                    return g, c
        raise KeyError(f"unknown camera {camera_id!r}")

    def all_cameras(self) -> list[tuple[GateConfig, CameraConfig]]:
        return [(g, c) for g in self.gates for c in g.cameras]


def load_config(path: str | os.PathLike[str], overrides: dict[str, Any] | None = None) -> ServiceConfig:
    """Load a YAML config, expand env vars and apply env overrides.

    Environment variables win over the file for deployment-specific values:
    ``BACKEND_URL``, ``ANPR_API_KEY``, ``IMAGE_ROOT``, ``ANPR_OUTBOX``.
    """
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    raw = _expand_env(raw)
    if overrides:
        raw = _deep_merge(raw, overrides)
    cfg = ServiceConfig.model_validate(raw)
    env = os.environ
    if env.get("BACKEND_URL"):
        cfg.backend.url = env["BACKEND_URL"]
    if env.get("ANPR_API_KEY"):
        cfg.backend.api_key = env["ANPR_API_KEY"]
    if env.get("IMAGE_ROOT"):
        cfg.image_root = env["IMAGE_ROOT"]
    if env.get("ANPR_OUTBOX"):
        cfg.outbox_path = env["ANPR_OUTBOX"]
    return cfg


def _deep_merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


_REMOTE_CAMERA_FIELDS = ("role", "rtsp_url", "side", "enabled")
_GEOMETRY_FIELDS = ("roi", "capture_line", "in_vector")


def apply_remote_config(cfg: ServiceConfig, remote: dict[str, Any]) -> ServiceConfig:
    """Overlay the backend's ``/api/anpr/config`` answer onto the local config.

    Remote values win for gate direction/name (the backend resolves
    time-of-day schedules), camera URL/role, merge/dedupe/confidence settings
    and state codes.  Camera geometry (roi, capture_line, in_vector) follows
    ``backend.geometry`` (default: YAML wins when it defines a capture line).
    Local-only fields (replay files, GStreamer pipelines, gate spans...) are
    kept.  Cameras or gates only present remotely are added.
    """
    data = cfg.model_dump(mode="json")
    gates_by_id = {g["id"]: g for g in data["gates"]}
    for rg in remote.get("gates", []) or []:
        gid = rg.get("id")
        if not gid:
            continue
        g = gates_by_id.get(gid)
        if g is None:
            g = {"id": gid, "name": rg.get("name", ""), "direction": rg.get("direction", "BOTH"), "cameras": []}
            data["gates"].append(g)
            gates_by_id[gid] = g
        if rg.get("name"):
            g["name"] = rg["name"]
        if rg.get("direction"):
            g["direction"] = str(rg["direction"]).upper()
        cams_by_id = {c["id"]: c for c in g["cameras"]}
        for rc in rg.get("cameras", []) or []:
            cid = rc.get("id")
            if not cid:
                continue
            c = cams_by_id.get(cid)
            if c is None:
                c = {"id": cid}
                g["cameras"].append(c)
                cams_by_id[cid] = c
            for f in _REMOTE_CAMERA_FIELDS:
                if rc.get(f) not in (None, ""):
                    c[f] = rc[f]
            remote_has_geometry = bool(rc.get("capture_line"))
            local_has_geometry = bool(c.get("capture_line"))
            if remote_has_geometry and (not local_has_geometry or cfg.backend.geometry == "prefer_backend"):
                for f in _GEOMETRY_FIELDS:
                    if rc.get(f):
                        c[f] = rc[f]
    remote_settings = remote.get("settings") or {}
    for k, v in remote_settings.items():
        if k in Settings.model_fields and v is not None:
            data["settings"][k] = v
    codes = remote_settings.get("state_codes")
    if isinstance(codes, list) and codes:
        data["plates"]["state_codes"] = [str(x).upper() for x in codes]
    return ServiceConfig.model_validate(data)
