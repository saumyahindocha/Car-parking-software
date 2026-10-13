"""DPDP Act 2023 support: retention purge of images, customer data export and erasure.

Erasure removes personal data that is not needed for accounting (phone, name, notes, images).
Financial records (ledger, payments, receipts) are retained as required by accounting law;
they keep the plate number but lose the phone number.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import utcnow
from ..models import (AnprEvent, LedgerEntry, MessageLog, ParkingSession, Pass, Payment, PaymentDispute, Receipt,
                      Vehicle)
from .settings import get_setting

log = logging.getLogger(__name__)
FULL_FRAME_KINDS = ("full_frame", "overview", "extra")


def _unlink(rel: Optional[str]) -> bool:
    if not rel:
        return False
    root = Path(get_settings().image_root).resolve()
    p = (root / rel).resolve()
    if root in p.parents and p.is_file():
        try:
            p.unlink()
            return True
        except OSError:
            log.warning("could not delete %s", p)
    return False


def purge_images(db: Session, now: Optional[datetime] = None, batch: int = 5000) -> dict:
    """Delete full frames older than N days and plate crops older than M days (configurable)."""
    now = now or utcnow()
    frames_cut = now - timedelta(days=int(get_setting(db, "retention_full_frames_days")))
    plates_cut = now - timedelta(days=int(get_setting(db, "retention_plate_images_days")))
    removed = {"full_frames": 0, "plate_crops": 0}
    for ev in db.scalars(select(AnprEvent).where(AnprEvent.ts < frames_cut).order_by(AnprEvent.ts).limit(batch)).all():
        imgs = dict(ev.images or {})
        changed = False
        for k in FULL_FRAME_KINDS:
            if k in imgs and imgs[k]:
                vals = imgs[k] if isinstance(imgs[k], list) else [imgs[k]]
                for v in vals:
                    removed["full_frames"] += int(_unlink(v))
                imgs.pop(k)
                changed = True
        if ev.ts < plates_cut and imgs.get("plate_crop"):
            removed["plate_crops"] += int(_unlink(imgs.pop("plate_crop")))
            changed = True
        if changed:
            imgs["purged_at"] = now.isoformat()
            ev.images = imgs
    db.flush()
    return removed


def export_vehicle(db: Session, vehicle_id: int) -> dict:
    v = db.get(Vehicle, vehicle_id)
    if v is None:
        raise LookupError("vehicle not found")

    def rows(model, *conds):
        out = []
        for r in db.scalars(select(model).where(*conds)).all():
            out.append({c.key: (getattr(r, c.key).isoformat() if isinstance(getattr(r, c.key), datetime) else getattr(r, c.key))
                        for c in model.__mapper__.column_attrs})
        return out

    return {
        "generated_at": utcnow().isoformat(),
        "vehicle": rows(Vehicle, Vehicle.id == vehicle_id)[0],
        "sessions": rows(ParkingSession, ParkingSession.vehicle_id == vehicle_id),
        "anpr_events": rows(AnprEvent, AnprEvent.vehicle_id == vehicle_id),
        "payments": rows(Payment, Payment.vehicle_id == vehicle_id),
        "ledger": rows(LedgerEntry, LedgerEntry.vehicle_id == vehicle_id),
        "passes": rows(Pass, Pass.vehicle_id == vehicle_id),
        "disputes": rows(PaymentDispute, PaymentDispute.vehicle_id == vehicle_id),
        "receipts": rows(Receipt, Receipt.payment_id.in_(select(Payment.id).where(Payment.vehicle_id == vehicle_id))),
    }


def erase_vehicle(db: Session, vehicle_id: int, reason: str) -> dict:
    if not reason.strip():
        raise ValueError("reason required")
    v = db.get(Vehicle, vehicle_id)
    if v is None:
        raise LookupError("vehicle not found")
    phone = v.phone
    images = 0
    for ev in db.scalars(select(AnprEvent).where(AnprEvent.vehicle_id == vehicle_id)).all():
        imgs = ev.images or {}
        for k, val in imgs.items():
            for x in (val if isinstance(val, list) else [val]):
                if isinstance(x, str):
                    images += int(_unlink(x))
        ev.images = {"erased": True}
    v.phone, v.name, v.notes = None, None, f"personal data erased: {reason}"
    for p in db.scalars(select(Payment).where(Payment.vehicle_id == vehicle_id, Payment.phone.is_not(None))).all():
        p.phone = None
    for r in db.scalars(select(Receipt).where(Receipt.payment_id.in_(select(Payment.id).where(Payment.vehicle_id == vehicle_id)))).all():
        r.phone = None
    if phone:
        for m in db.scalars(select(MessageLog).where(MessageLog.to == phone)).all():
            m.to, m.body, m.variables = "erased", "(erased)", {}
    db.flush()
    return {"vehicle_id": vehicle_id, "images_deleted": images, "phone_removed": bool(phone)}
