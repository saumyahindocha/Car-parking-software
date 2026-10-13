"""Turn merged gate events into the backend event contract (and write their images)."""

from __future__ import annotations

import logging
from typing import Any

from .aggregator import MergedEvent
from .imaging import ImageStore
from .metrics import now_ms

log = logging.getLogger(__name__)


class EventBuilder:
    """Writes an event's JPEGs to the shared image root and builds the JSON payload.

    Payload (``POST /api/anpr/events``)::

        {"event_id", "gate_id", "camera_ids", "direction", "wrong_way",
         "vehicle_class", "ts_ms", "status", "plate", "confidence",
         "candidates": [{"plate", "confidence"}] (top-3),
         "images": {"plate_crop", "full_frame", "overview", "extra": [...]},
         "latency_ms"}
    """

    def __init__(self, store: ImageStore) -> None:
        self.store = store

    def build(self, ev: MergedEvent) -> dict[str, Any]:
        images: dict[str, Any] = {"plate_crop": None, "full_frame": None, "overview": None, "extra": []}
        p = ev.primary
        write = self.store.write
        try:
            if p.plate_jpeg:
                images["plate_crop"] = write(ev.gate_id, ev.event_id, "plate_crop", ev.ts_ms, p.plate_jpeg)
            if p.frame_jpeg:
                images["full_frame"] = write(ev.gate_id, ev.event_id, "full_frame", ev.ts_ms, p.frame_jpeg)
            if ev.overview_jpeg:
                images["overview"] = write(ev.gate_id, ev.event_id, "overview", ev.ts_ms, ev.overview_jpeg)
            for i, r in enumerate(ev.others, 1):
                if r.plate_jpeg:
                    images["extra"].append(
                        write(ev.gate_id, ev.event_id, f"extra{i}_{r.camera_id}_plate_crop", ev.ts_ms, r.plate_jpeg)
                    )
                if r.frame_jpeg:
                    images["extra"].append(
                        write(ev.gate_id, ev.event_id, f"extra{i}_{r.camera_id}_full_frame", ev.ts_ms, r.frame_jpeg)
                    )
        except OSError:
            # Never lose the event because the image disk is full / unmounted.
            log.exception("gate=%s event=%s: writing images failed", ev.gate_id, ev.event_id)
        latency = max(0, now_ms() - ev.first_grab_wall_ms) if ev.first_grab_wall_ms else 0
        return {
            "event_id": ev.event_id,
            "gate_id": ev.gate_id,
            "camera_ids": ev.camera_ids,
            "direction": ev.direction.value,
            "wrong_way": bool(ev.wrong_way),
            "vehicle_class": ev.vehicle_class.value,
            "ts_ms": int(ev.ts_ms),
            "status": ev.status.value,
            "plate": ev.plate,
            "confidence": float(ev.confidence),
            "candidates": [c.to_dict() for c in ev.candidates[:3]],
            "images": images,
            "latency_ms": int(latency),
        }
