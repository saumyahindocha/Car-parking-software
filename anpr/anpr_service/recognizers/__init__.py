"""Pluggable plate-recognition engines (see :class:`PlateRecognizer`)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import PlateRecognizer, RecognizerUnavailable, associate_plates

if TYPE_CHECKING:
    from ..config import ServiceConfig

__all__ = ["PlateRecognizer", "RecognizerUnavailable", "associate_plates", "create_recognizer"]


def create_recognizer(cfg: "ServiceConfig") -> PlateRecognizer:
    """Instantiate the engine selected by ``recognizer.kind``.

    Called once per camera worker: engines may keep per-stream state (the
    classical engine's background model) and are not shared between cameras.
    """
    kind = cfg.recognizer.kind
    if kind == "classical":
        from .classical import ClassicalRecognizer

        return ClassicalRecognizer(cfg.recognizer.classical, cfg.pipeline.min_plate_width_px)
    if kind == "onnx":
        from .onnx_local import LocalOnnxRecognizer

        return LocalOnnxRecognizer(cfg.recognizer.onnx)
    if kind == "commercial":
        from .commercial import CommercialApiRecognizer

        return CommercialApiRecognizer(cfg.recognizer.commercial)
    raise ValueError(f"unknown recognizer kind {kind!r}")
