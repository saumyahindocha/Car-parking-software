"""Digital receipts: every confirmed payment gets a short link; delivered by SMS/WhatsApp or QR."""
from __future__ import annotations

import secrets

from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import ParkingSession, Pass, PassType, Payment, Receipt, User, Vehicle
from . import notify
from .lookup import site_tz
from .settings import get_setting

_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _code() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(8))


def issue_receipt(db: Session, p: Payment) -> Receipt:
    if p.receipt_id:
        return db.get(Receipt, p.receipt_id)
    veh = db.get(Vehicle, p.vehicle_id)
    sess = db.get(ParkingSession, p.session_id) if p.session_id else None
    tz = site_tz()
    code = _code()
    while db.query(Receipt.id).filter(Receipt.code == code).first():
        code = _code()
    at = (p.confirmed_at or p.created_at).astimezone(tz)
    number = f"R{at:%y%m%d}-{p.id:07d}"
    link = f"{get_settings().public_receipt_base.rstrip('/')}/{code}"
    gst_rate = float(get_setting(db, "gst_rate_percent") or 0)
    data = {
        "number": number, "link": link, "lot_name": get_setting(db, "lot_name"),
        "lot_address": get_setting(db, "lot_address"), "gstin": get_setting(db, "gstin") or None,
        "plate": veh.display_plate, "vehicle_class": veh.vehicle_class, "mode": p.mode,
        "amount_paise": p.amount_paise, "base_paise": p.base_paise, "dues_cleared_paise": p.dues_paise,
        "entry_time": sess.entry_at.astimezone(tz).isoformat() if sess and sess.entry_at else None,
        "duration_paid_minutes": p.duration_minutes, "paid_at": at.isoformat(),
        "upi_ref": (p.utr or p.txn_ref) if p.mode == "UPI" else None, "txn_ref": p.txn_ref,
        "collected_by": (db.get(User, p.collected_by).name if p.collected_by else "Self-pay"),
        "footer": get_setting(db, "receipt_footer"),
    }
    if gst_rate > 0:
        taxable = round(p.amount_paise * 100 / (100 + gst_rate))
        data.update(gst_rate_percent=gst_rate, taxable_paise=taxable, gst_paise=p.amount_paise - taxable)
    if p.pass_id:
        ps = db.get(Pass, p.pass_id)
        pt = db.get(PassType, ps.pass_type_id)
        data["pass"] = {"type": pt.name, "valid_from": ps.starts_at.astimezone(tz).date().isoformat(),
                        "valid_till": ps.ends_at.astimezone(tz).date().isoformat()}
    phone = p.phone or veh.phone
    m_pref = "WHATSAPP" if notify.get_messenger().prefer_whatsapp else "SMS"
    r = Receipt(code=code, number=number, payment_id=p.id, pass_id=p.pass_id, data=data, phone=phone,
                channel=m_pref if phone else "QR", delivery_status="PENDING")
    db.add(r)
    db.flush()
    p.receipt_id = r.id
    if phone:
        notify.receipt_message(db, r)
    return r


def mark_shown(db: Session, receipt: Receipt) -> Receipt:
    """Worker app confirms the receipt QR was shown full-screen to the customer."""
    if receipt.channel == "QR":
        receipt.delivery_status = "SHOWN"
    return receipt


def send_to_phone(db: Session, receipt: Receipt, phone: str) -> Receipt:
    """Customer gave their number after all: switch QR receipt to SMS/WhatsApp."""
    receipt.phone = phone
    receipt.channel = "WHATSAPP" if notify.get_messenger().prefer_whatsapp else "SMS"
    receipt.delivery_status = "PENDING"
    veh = db.get(Vehicle, db.get(Payment, receipt.payment_id).vehicle_id)
    if not veh.phone:
        veh.phone = phone
    notify.receipt_message(db, receipt)
    return receipt
