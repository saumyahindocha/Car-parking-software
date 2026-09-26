"""Outbound customer messages. Messages are queued in message_log (status PENDING) and sent by
the `deliver_messages` job, so a slow or absent internet link never blocks a worker."""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..adapters.messaging import get_messenger
from ..db import utcnow
from ..models import MessageLog, ParkingSession, Receipt, Vehicle
from .settings import get_setting

log = logging.getLogger(__name__)


def _rupees(p: int) -> str:
    return f"{p / 100:.0f}" if p % 100 == 0 else f"{p / 100:.2f}"


def enqueue(db: Session, to: str, template: str, body: str, channel: Optional[str] = None,
            variables: Optional[dict] = None, receipt_id: Optional[int] = None) -> MessageLog:
    m = get_messenger()
    ch = channel or ("WHATSAPP" if m.prefer_whatsapp else "SMS")
    row = MessageLog(channel=ch, to=to, template=template, body=body, status="PENDING",
                     variables=variables or {}, receipt_id=receipt_id)
    db.add(row)
    db.flush()
    return row


def receipt_message(db: Session, receipt: Receipt) -> None:
    d = receipt.data
    if d.get("link"):
        body = (f"{d.get('lot_name')}: Rs {_rupees(d['amount_paise'])} received for {d['plate']} "
                f"({d.get('mode')}). Receipt {receipt.number}: {d['link']}")
    else:  # no customer website: the SMS itself is the receipt
        body = d.get("text") or f"{d.get('lot_name')}: Rs {_rupees(d['amount_paise'])} received for {d['plate']}"
    enqueue(db, receipt.phone, "receipt", body,
            variables={"plate": d["plate"], "amount": _rupees(d["amount_paise"]), "receipt": receipt.number, "link": d.get("link") or ""},
            channel="WHATSAPP" if receipt.channel == "WHATSAPP" else "SMS", receipt_id=receipt.id)


def settlement(db: Session, vehicle: Vehicle, sess: ParkingSession) -> None:
    bal = vehicle.balance_paise
    status = f"Rs {_rupees(bal)} due on next visit" if bal > 0 else (f"credit Rs {_rupees(-bal)}" if bal < 0 else "fully settled")
    body = (f"{get_setting(db, 'lot_name')}: {vehicle.display_plate} exited. Charge Rs {_rupees(sess.charge_paise or 0)} "
            f"for actual time. Balance: {status}.")
    enqueue(db, vehicle.phone, "settlement", body,
            variables={"plate": vehicle.display_plate, "charge": _rupees(sess.charge_paise or 0), "balance": status})


def pass_reminder(db: Session, vehicle: Vehicle, days_left: int, ends_on: str, link: str) -> None:
    renew = f"Renew: {link}" if link else "Renew with any parking attendant."
    body = (f"{get_setting(db, 'lot_name')}: parking pass for {vehicle.display_plate} expires on {ends_on} "
            f"({days_left} day{'s' if days_left != 1 else ''}). {renew}")
    enqueue(db, vehicle.phone, "pass_reminder", body,
            variables={"plate": vehicle.display_plate, "date": ends_on, "link": link})


def deliver_pending(db: Session, limit: int = 200) -> int:
    """Send queued messages; update linked receipts' delivery status. Returns count attempted."""
    m = get_messenger()
    rows = db.scalars(select(MessageLog).where(MessageLog.status.in_(["PENDING", "RETRY"]))
                      .order_by(MessageLog.id).limit(limit)).all()
    for row in rows:
        sender = m.whatsapp if row.channel == "WHATSAPP" else m.sms
        variables = row.variables or {}
        res = sender.send(row.to, row.template, variables, row.body)
        if res.status == "FAILED" and row.channel == "WHATSAPP":
            res = m.sms.send(row.to, row.template, variables, row.body)  # fall back to SMS
        row.attempts = (row.attempts or 0) + 1
        row.status = res.status if res.status != "FAILED" else ("RETRY" if row.attempts < 5 else "FAILED")
        row.provider_ref = res.provider_ref or res.error
        if row.receipt_id:
            rec = db.get(Receipt, row.receipt_id)
            if rec is not None:
                rec.delivery_status = "SENT" if res.status == "SENT" else ("FAILED" if row.status == "FAILED" else "PENDING")
                rec.delivered_at = utcnow() if res.status == "SENT" else None
    db.flush()
    return len(rows)
