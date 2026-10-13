#!/usr/bin/env python3
"""Populate a demo backend (PARK_DEMO_MODE=true) with enough data to exercise every dashboard page.

Usage:  python dashboard/scripts/demo_data.py [--base http://localhost:8000]

Posts ANPR events with generated plate images (through the real /api/anpr/events ingest, so thumbnails
work), device heartbeats, worker shifts with UPI and cash collections, an offline UPI claim, a pending
cash handover, a dispute and a zone assignment. Standard library + Pillow only.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import random
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # images are optional
    Image = None

ANPR_KEY = "dev-anpr-key"
DEVICE_KEY = "dev-device-key"


class Api:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")

    def call(self, method: str, path: str, body=None, token: str | None = None, headers: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                txt = r.read().decode()
                return json.loads(txt) if txt else None
        except urllib.error.HTTPError as e:
            print(f"  ! {method} {path} -> {e.code}: {e.read().decode()[:200]}")
            return None

    def login(self, username: str, password: str | None = None, pin: str | None = None) -> str:
        r = self.call("POST", "/api/auth/login", {"username": username, "password": password, "pin": pin})
        return r["token"]


def _font(size: int):
    for f in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"):
        try:
            return ImageFont.truetype(f, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _jpeg(img) -> str:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return base64.b64encode(buf.getvalue()).decode()


def images_for(plate: str | None, gate: str) -> dict:
    if Image is None:
        return {}
    crop = Image.new("RGB", (240, 110), (250, 250, 245))
    d = ImageDraw.Draw(crop)
    d.rectangle([2, 2, 237, 107], outline=(20, 20, 20), width=3)
    if plate:
        top, bottom = plate[:4], plate[4:]
        d.text((120, 30), top, fill=(10, 10, 10), font=_font(34), anchor="mm")
        d.text((120, 78), bottom, fill=(10, 10, 10), font=_font(34), anchor="mm")
    else:
        d.text((120, 55), "? ? ?", fill=(120, 120, 120), font=_font(34), anchor="mm")
    frame = Image.new("RGB", (640, 360), (70, 78, 86))
    d = ImageDraw.Draw(frame)
    d.polygon([(200, 360), (440, 360), (380, 120), (260, 120)], fill=(90, 98, 106))
    d.rectangle([280, 150, 360, 300], fill=(30, 30, 40))
    frame.paste(crop.resize((96, 44)), (272, 240))
    d.text((10, 10), f"{gate} ANPR  {datetime.now():%H:%M:%S}", fill=(255, 255, 255), font=_font(16))
    ov = Image.new("RGB", (640, 360), (60, 90, 70))
    d = ImageDraw.Draw(ov)
    d.rectangle([0, 200, 640, 360], fill=(80, 80, 80))
    for x in (180, 300, 420):
        d.rectangle([x, 220, x + 40, 300], fill=(20, 20, 30))
    d.text((10, 10), f"{gate} overview", fill=(255, 255, 255), font=_font(16))
    return {"plate_crop": _jpeg(crop), "full_frame": _jpeg(frame), "overview": _jpeg(ov)}


def event(api: Api, gate: str, direction: str, plate: str | None, conf: float = 0.93, cands=None, cam_side="L"):
    body = {"event_id": str(uuid.uuid4()), "gate_id": gate, "camera_ids": [f"{gate}-{cam_side}"], "direction": direction,
            "vehicle_class": "BIKE", "ts_ms": int(time.time() * 1000), "status": "READ" if plate else "UNREAD",
            "plate": plate, "confidence": conf if plate else 0.0,
            "candidates": cands if cands is not None else ([{"plate": plate, "confidence": conf}] if plate else []),
            "images": {}, "images_b64": images_for(plate, gate), "latency_ms": random.randint(400, 1300)}
    return api.call("POST", "/api/anpr/events", body, headers={"X-Device-Key": ANPR_KEY})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--heartbeats-only", action="store_true", help="only refresh device heartbeats")
    args = ap.parse_args()
    api = Api(args.base)
    if args.heartbeats_only:
        heartbeats(api)
        return
    random.seed(7)
    sup = api.login("sup1", password="super123")
    admin = api.login("admin", password="admin123")

    heartbeats(api)

    print("zone assignments")
    users = api.call("GET", "/api/users", token=admin) or []
    _rest(api, sup, admin, users)


def heartbeats(api: Api) -> None:
    print("heartbeats")
    for cam in ("G1-L", "G1-R", "G1-O", "G2-L", "G2-R", "G2-O"):
        api.call("POST", "/api/devices/heartbeat", {
            "device_id": cam, "kind": "CAMERA", "gate_id": cam[:2], "name": f"Camera {cam}",
            "metrics": {"fps": round(random.uniform(11, 15), 1), "read_rate": round(random.uniform(0.9, 0.99), 3),
                        "stream_ok": True, "last_frame_at": datetime.now(timezone.utc).isoformat()}},
            headers={"X-Device-Key": DEVICE_KEY})
    for g in ("G1", "G2"):
        api.call("POST", "/api/devices/heartbeat", {"device_id": f"ALERT-{g}", "kind": "ALERT_UNIT", "gate_id": g,
                                                    "name": f"Exit display {g}", "metrics": {"temp_c": 48}},
                 headers={"X-Device-Key": DEVICE_KEY})


def _rest(api: Api, sup: str, admin: str, users: list) -> None:
    uid = {u["username"]: u["id"] for u in users}
    now = datetime.now(timezone.utc)
    for uname, zone in (("w1", 1), ("w2", 2)):
        api.call("POST", "/api/zones/assignments", {"zone_id": zone, "user_id": uid[uname],
                                                    "starts_at": (now - timedelta(hours=2)).isoformat(),
                                                    "ends_at": (now + timedelta(hours=6)).isoformat(),
                                                    "shift_label": "Morning"}, token=sup)

    print("entries")
    states = ["MH43", "MH04", "MH12", "MH02", "MH05"]
    plates = []
    for i in range(18):
        p = f"{random.choice(states)}{random.choice('ABCDEFGHJK')}{random.choice('ABCDEFGHJK')}{random.randint(1000, 9999)}"
        plates.append(p)
        event(api, "G1", "IN", p, cam_side=random.choice("LR"))
    # two close plates to make an ambiguous exit later
    event(api, "G1", "IN", "MH43AB1234")
    event(api, "G1", "IN", "MH43AB1284")
    for _ in range(3):
        event(api, "G1", "IN", None)

    print("worker collections")
    w1 = api.login("w1", pin="1111")
    w2 = api.login("w2", pin="2222")
    api.call("POST", "/api/shifts/open", {"zone_id": 1}, token=w1)
    api.call("POST", "/api/shifts/open", {"zone_id": 2}, token=w2)
    lst = api.call("GET", "/api/collect/list", token=w1) or []
    for i, row in enumerate(lst[:12]):
        tok = w1 if i % 2 == 0 else w2
        body = {"purpose": "SESSION", "session_id": row["session_id"], "duration_minutes": 240,
                "client_uuid": str(uuid.uuid4())}
        if i % 3 == 0:
            api.call("POST", "/api/payments/cash", {**body, "phone": "98765432%02d" % i if i % 2 else None}, token=tok)
        else:
            p = api.call("POST", "/api/payments/upi", body, token=tok)
            if p and i % 4 != 1:
                api.call("POST", f"/api/demo/pay/{p['id']}")
            elif p and i == 1:
                api.call("POST", f"/api/payments/{p['id']}/claim-offline", token=tok)

    print("exits")
    for p in plates[:5]:
        event(api, "G2", "OUT", p)
    # two open sessions one edit away from the exit read → AMBIGUOUS review with session candidates
    event(api, "G1", "IN", "MH14CD5678")
    event(api, "G1", "IN", "MH14CD5578")
    event(api, "G2", "OUT", "MH14CD5978", conf=0.7,
          cands=[{"plate": "MH14CD5978", "confidence": 0.7}, {"plate": "MH14CD5678", "confidence": 0.2}])
    event(api, "G1", "OUT", plates[7], cam_side="R")  # wrong way on an IN gate (if the gate is IN only)
    event(api, "G2", "OUT", "KA01ZZ9999")  # never entered → review (no match)
    event(api, "G1", "IN", plates[15])  # entered again without an exit → ORPHAN_ENTRY

    print("handover + dispute")
    api.call("POST", "/api/cash/handovers", {"amount_paise": 1000, "denominations": {"10": 1}}, token=w1)
    if lst:
        api.call("POST", "/api/disputes", {"vehicle_id": lst[-1]["vehicle_id"], "claimed_paise": 1000,
                                           "claimed_mode": "CASH", "note": "Customer says paid cash to worker"}, token=sup)
    print("done")


if __name__ == "__main__":
    main()
