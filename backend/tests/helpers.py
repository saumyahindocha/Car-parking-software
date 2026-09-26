import uuid
from datetime import datetime

from app.domain.sessions import ingest_event


def event(db, direction, plate, ts: datetime, *, gate=None, status="READ", conf=0.95, candidates=None, vclass="BIKE"):
    gate = gate or ("G1" if direction == "IN" else "G2")
    payload = {
        "event_id": str(uuid.uuid4()), "gate_id": gate, "camera_ids": [f"{gate}-L"], "direction": direction,
        "wrong_way": False, "vehicle_class": vclass, "ts_ms": int(ts.timestamp() * 1000), "status": status,
        "plate": plate, "confidence": conf, "candidates": candidates or ([{"plate": plate, "confidence": conf}] if plate else []),
        "images": {"plate_crop": "x/crop.jpg", "full_frame": "x/full.jpg", "overview": "x/ov.jpg"}, "latency_ms": 500,
    }
    res = ingest_event(db, payload)
    db.flush()
    return res
