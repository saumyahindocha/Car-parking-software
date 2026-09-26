"""Seed the relay with demo data (no edge server needed): `python -m relay.demo [--reset]`.

Writes the same shape of data the edge pushes (see sync.apply_push) into RELAY_DATABASE_URL:
recently entered bikes with blurred thumbnails and quotes, a vehicle with dues, pass types, lot
settings and one receipt. Then run `RELAY_DEMO_MODE=true RELAY_COOKIE_SECURE=false uvicorn relay.main:app --port 8080`.
"""
from __future__ import annotations

import argparse
import base64
import io
import random
from datetime import datetime, timedelta, timezone
from typing import Optional

from .config import get_settings
from .db import Base, make_engine, make_sessionmaker
from .plates import display, mask
from .security import edge_phone_hash
from .sync import apply_push

PLATES = ["MH12AB1234", "MH14CD5678", "MH12EF9012", "MH43GH3456", "MH12JK7890", "MH04LM2345", "MH12NP6789",
          "MH14QR0123", "MH12ST4567", "MH05UV8901"]
DEMO_PHONE = "9876543210"


def thumb(plate: str) -> Optional[str]:
    try:
        from PIL import Image, ImageDraw, ImageFilter
    except ImportError:  # pragma: no cover
        return None
    im = Image.new("RGB", (200, 100), (238, 238, 230))
    d = ImageDraw.Draw(im)
    d.rectangle((4, 4, 195, 95), outline=(0, 0, 0), width=3)
    d.text((22, 40), display(plate), fill=(0, 0, 0))
    box = (60, 0, 160, 100)
    im.paste(im.crop(box).filter(ImageFilter.GaussianBlur(6)), box)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=60)
    return base64.b64encode(buf.getvalue()).decode()


def demo_body(now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    s = get_settings()
    rng = random.Random(4)
    entries = []
    for i, p in enumerate(PLATES):
        dues = 3000 if p == "MH14CD5678" else 0
        entries.append({"session_id": 1000 + i, "plate": p, "masked_plate": mask(p), "vehicle_class": "BIKE",
                        "entry_at": (now - timedelta(minutes=10 + i * rng.randint(9, 25))).isoformat(),
                        "status": "PREPAID" if i == 3 else "OPEN", "gate_id": "G1",
                        "dues_paise": dues, "credit_paise": 0,
                        "quotes": {"120": 1000 + dues, "240": 2000 + dues, "480": 3000 + dues, "720": 3000 + dues,
                                   "1440": 6000 + dues},
                        "thumb_b64": thumb(p)})
    hist = [{"entry_at": (now - timedelta(days=d, hours=10)).isoformat(),
             "exit_at": (now - timedelta(days=d, hours=1)).isoformat(), "charge_paise": 3000,
             "status": "CLOSED"} for d in (1, 2, 5)]
    vehicles = [
        {"plate": "MH14CD5678", "vehicle_class": "BIKE", "balance_paise": 3000,
         "phone_hash": edge_phone_hash(s.relay_api_key, DEMO_PHONE), "pass": None, "history": hist},
        {"plate": "MH12AB1234", "vehicle_class": "BIKE", "balance_paise": 0, "phone_hash": None,
         "pass": {"pass_type": "Monthly", "pass_type_id": 1, "vehicle_class": "BIKE",
                  "starts_on": (now - timedelta(days=26)).date().isoformat(),
                  "ends_on": (now + timedelta(days=4)).date().isoformat(), "status": "ACTIVE"}, "history": []},
    ]
    receipt = {"code": "DemoRc01", "number": "R" + now.strftime("%y%m%d") + "-0000001",
               "created_at": now.isoformat(),
               "data": {"number": "R" + now.strftime("%y%m%d") + "-0000001", "lot_name": "Station Parking",
                        "lot_address": "Station Road", "gstin": None, "plate": "MH 12 EF 9012",
                        "vehicle_class": "BIKE", "mode": "UPI", "amount_paise": 2000, "base_paise": 2000,
                        "dues_cleared_paise": 0, "entry_time": (now - timedelta(hours=1)).isoformat(),
                        "duration_paid_minutes": 240, "paid_at": now.isoformat(), "upi_ref": "412345678901",
                        "txn_ref": "PSRSX0A1B2C", "collected_by": "Self-pay",
                        "footer": "Final charge is calculated on actual time; any difference is adjusted on "
                                  "your next visit."}}
    return {"generated_at": now.isoformat(), "full": True, "entries": entries, "vehicles": vehicles,
            "receipts": [receipt],
            "pass_types": [{"id": 1, "vehicle_class": "BIKE", "name": "Monthly", "period_unit": "MONTH",
                            "period_value": 1, "price_paise": 50000},
                           {"id": 3, "vehicle_class": "CAR", "name": "Monthly", "period_unit": "MONTH",
                            "period_value": 1, "price_paise": 150000}],
            "settings": {"lot_name": "Station Parking",
                         "receipt_footer": "Final charge is calculated on actual time; any difference is adjusted "
                                           "on your next visit.",
                         "duration_buttons": [120, 240, 480, 720, 1440], "upi_vpa": "parking@upi",
                         "upi_payee_name": "Station Parking", "gstin": "", "lot_address": "Station Road",
                         "gst_rate_percent": 0, "pass_expiry_warn_days": 5, "site_timezone": "Asia/Kolkata"}}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reset", action="store_true", help="drop and recreate the relay tables first")
    a = ap.parse_args()
    eng = make_engine(get_settings().database_url)
    if a.reset:
        Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    db = make_sessionmaker(eng)()
    counts = apply_push(db, demo_body())
    db.commit()
    db.close()
    print(f"demo data loaded: {counts}")
    print(f"  dues demo: plate MH14CD5678, mobile {DEMO_PHONE} (OTP is shown on screen in demo mode)")
    print("  pass demo: plate MH12AB1234 (pass expires in 4 days), any mobile")
    print("  receipt:   /r/DemoRc01")


if __name__ == "__main__":
    main()
