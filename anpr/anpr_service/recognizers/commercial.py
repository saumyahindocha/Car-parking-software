"""Adapter for Plate Recognizer (platerecognizer.com) Snapshot Cloud API / on-prem SDK.

* Cloud: ``POST https://api.platerecognizer.com/v1/plate-reader/`` with
  ``Authorization: Token <key>`` (key from the env var named by
  ``commercial.api_key_env``, default ``PLATE_RECOGNIZER_API_KEY``).
* On-prem Snapshot SDK (Docker, licensed per camera): set ``api_url`` to
  ``http://<sdk-host>:8080/v1/plate-reader/``; no token needed.

``regions=in`` enables the Indian plate grammar.  The API detects and reads
in one call, so :meth:`analyze` returns vehicles with ``plate.read`` already
filled in; the local tracker/voting/merge logic still applies on top.  Every
analysed frame is a billable lookup: raise ``pipeline.process_every_n`` or
``commercial.min_interval_s`` accordingly.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import httpx
import numpy as np

from ..config import CommercialConfig
from ..imaging import encode_jpeg
from ..plates import normalize
from ..types import OcrResult, PlateObservation, VehicleClass, VehicleObservation
from .base import PlateRecognizer, RecognizerUnavailable

log = logging.getLogger(__name__)

_BIKE_TYPES = {"motorcycle", "scooter", "motorbike", "bike"}
_CAR_TYPES = {"sedan", "suv", "car", "van", "pickup truck", "pickup", "hatchback", "wagon"}


def _vclass(vtype: str | None) -> VehicleClass:
    t = (vtype or "").strip().lower()
    if t in _BIKE_TYPES:
        return VehicleClass.BIKE
    if t in _CAR_TYPES:
        return VehicleClass.CAR
    return VehicleClass.OTHER


def _box(d: dict[str, Any] | None) -> tuple[float, float, float, float] | None:
    if not d:
        return None
    try:
        return (float(d["xmin"]), float(d["ymin"]), float(d["xmax"]), float(d["ymax"]))
    except (KeyError, TypeError, ValueError):
        return None


class CommercialApiRecognizer(PlateRecognizer):
    name = "commercial"

    def __init__(
        self,
        cfg: CommercialConfig,
        api_key: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.cfg = cfg
        key = api_key if api_key is not None else os.environ.get(cfg.api_key_env, "")
        if not key and "platerecognizer.com" in cfg.api_url:
            raise RecognizerUnavailable(f"Plate Recognizer cloud API needs an API key in ${cfg.api_key_env}")
        headers = {"Authorization": f"Token {key}"} if key else {}
        self._client = httpx.Client(headers=headers, timeout=cfg.timeout_s, transport=transport)
        self._last_call = 0.0
        self._last_error_log = 0.0

    def close(self) -> None:
        self._client.close()

    def _call(self, img: np.ndarray) -> list[dict[str, Any]]:
        files = {"upload": ("frame.jpg", encode_jpeg(img, 90), "image/jpeg")}
        data: dict[str, Any] = {"regions": self.cfg.regions, "config": json.dumps({"mode": "fast"})}
        if self.cfg.mmc:
            data["mmc"] = "true"
        try:
            r = self._client.post(self.cfg.api_url, files=files, data=data)
        except httpx.HTTPError as exc:
            self._log_error(f"request failed: {exc}")
            return []
        if r.status_code not in (200, 201):
            self._log_error(f"HTTP {r.status_code}: {r.text[:200]}")
            return []
        try:
            payload = r.json()
        except ValueError:
            self._log_error("invalid JSON response")
            return []
        results = payload.get("results") if isinstance(payload, dict) else None
        return results if isinstance(results, list) else []

    def _log_error(self, msg: str) -> None:
        now = time.monotonic()
        if now - self._last_error_log > 30:
            log.warning("Plate Recognizer API: %s", msg)
            self._last_error_log = now

    @staticmethod
    def _ocr(result: dict[str, Any]) -> OcrResult | None:
        text = normalize(result.get("plate"))
        if not text:
            return None
        score = float(result.get("score", 0.0))
        return OcrResult(text=text, char_confs=[score] * len(text), rows=1)

    def analyze(self, frame: np.ndarray, roi_mask: np.ndarray | None = None) -> list[VehicleObservation]:
        now = time.monotonic()
        if self.cfg.min_interval_s > 0 and now - self._last_call < self.cfg.min_interval_s:
            return []
        self._last_call = now
        out: list[VehicleObservation] = []
        h, w = frame.shape[:2]
        for res in self._call(frame):
            pbox = _box(res.get("box"))
            if pbox is None:
                continue
            vehicle = res.get("vehicle") or {}
            vbox = _box(vehicle.get("box"))
            if vbox is None or (vbox[2] - vbox[0]) <= 0:
                pw, ph = pbox[2] - pbox[0], pbox[3] - pbox[1]
                vbox = (max(0.0, pbox[0] - 1.5 * pw), max(0.0, pbox[1] - 6 * ph),
                        min(float(w), pbox[2] + 1.5 * pw), min(float(h), pbox[3] + 2 * ph))
            plate = PlateObservation(box=pbox, score=float(res.get("dscore", 0.0) or 0.0), read=self._ocr(res))
            out.append(VehicleObservation(box=vbox, vehicle_class=_vclass(vehicle.get("type")),
                                          score=float(vehicle.get("score", 0.5) or 0.5), plate=plate))
        return out

    def read_plate(self, crop: np.ndarray) -> OcrResult | None:
        results = self._call(crop)
        if not results:
            return None
        best = max(results, key=lambda r: float(r.get("score", 0.0)))
        return self._ocr(best)
