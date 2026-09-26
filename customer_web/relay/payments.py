"""Relay payments: create a UPI intent, confirm it from the gateway (webhook or status poll), and only
then queue SELF_PAY_PAID / DUES_PAID / PASS_PAID (+ CONTACT_VERIFIED) for the edge.

Transaction references (UPI `tr`, <= 35 chars, [0-9A-Z]):
  self-pay  PS<session id base36>X<6 hex>      same shape as the edge's make_txn_ref('S', session)
  dues      PRD<relay intent id base36>X<6 hex>
  pass      PRP<relay intent id base36>X<6 hex>
The edge dedupes by txn_ref, so any unique reference works; the PR* prefixes deliberately do NOT
match the edge's `^P[SPV]` parser, so a relay dues/pass credit that shows up in the edge's gateway
settlement before the message is pulled is reported as an unknown credit with no (wrong) vehicle id.
"""
from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .db import utcnow
from .gateway import GatewayUnavailable, PaymentGateway, StatusResult, WebhookEvent
from .models import Entry, Outbox, PaymentIntent, PassType, Receipt, Vehicle
from .sync import enqueue

log = logging.getLogger(__name__)


class PaymentError(ValueError):
    pass


def b36(n: int) -> str:
    chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    out = ""
    while True:
        n, r = divmod(n, 36)
        out = chars[r] + out
        if n == 0:
            return out


def make_txn_ref(kind: str, ident: int) -> str:
    prefix = {"SELF_PAY": "PS", "DUES": "PRD", "PASS": "PRP"}[kind]
    return f"{prefix}{b36(ident)}X{secrets.token_hex(3).upper()}"


@dataclass
class Amounts:
    amount_paise: int
    base_paise: int
    dues_paise: int


def pending_relay_paid(db: Session, plate: str, since: Optional[datetime]) -> int:
    """Dues paid on the relay that the last pushed balance may not include yet."""
    stmt = select(PaymentIntent).where(PaymentIntent.plate == plate, PaymentIntent.status == "PAID",
                                       PaymentIntent.kind.in_(["SELF_PAY", "DUES"]))
    if since is not None:
        stmt = stmt.where(PaymentIntent.paid_at > since)
    return sum(p.dues_paise for p in db.scalars(stmt).all())


def outstanding_dues(db: Session, vehicle: Optional[Vehicle]) -> int:
    if vehicle is None:
        return 0
    return max(0, vehicle.balance_paise - pending_relay_paid(db, vehicle.plate, vehicle.updated_at))


def entry_is_paid(db: Session, entry: Entry) -> bool:
    if entry.status == "PREPAID" or entry.relay_paid_at is not None:
        return True
    return db.scalar(select(PaymentIntent.id).where(PaymentIntent.session_id == entry.session_id,
                                                    PaymentIntent.status == "PAID").limit(1)) is not None


def self_pay_amounts(db: Session, entry: Entry, duration: int) -> Amounts:
    amount = entry.quotes.get(str(duration))
    if amount is None:
        raise PaymentError("unknown duration")
    amount = int(amount)
    if amount <= 0:
        raise PaymentError("nothing to pay")
    veh = db.get(Vehicle, entry.plate)
    # quotes already include the vehicle's dues (edge build_push: charge + balance)
    dues = min(amount, max(0, veh.balance_paise)) if veh else 0
    return Amounts(amount, amount - dues, dues)


def _new_intent(db: Session, gw: PaymentGateway, *, kind: str, plate: str, vehicle_class: str, amounts: Amounts,
                qr_expiry_s: int, session_id: Optional[int] = None, duration: Optional[int] = None,
                pass_type_id: Optional[int] = None, phone: Optional[str] = None, contact_verified: bool = False,
                plate_revealed: bool = False, description: str = "Parking") -> PaymentIntent:
    now = utcnow()
    intent = PaymentIntent(token=secrets.token_urlsafe(18), kind=kind, plate=plate, plate_revealed=plate_revealed,
                           session_id=session_id, vehicle_class=vehicle_class, duration_minutes=duration,
                           amount_paise=amounts.amount_paise, base_paise=amounts.base_paise,
                           dues_paise=amounts.dues_paise, pass_type_id=pass_type_id, phone=phone,
                           queue_contact_verified=contact_verified, txn_ref="pending-" + secrets.token_hex(8),
                           gateway=gw.name, status="PENDING", created_at=now,
                           expires_at=now + timedelta(seconds=qr_expiry_s))
    db.add(intent)
    db.flush()
    intent.txn_ref = make_txn_ref(kind, session_id if kind == "SELF_PAY" and session_id else intent.id)
    try:
        qr = gw.create_upi_qr(amounts.amount_paise, intent.txn_ref, description, qr_expiry_s)
    except GatewayUnavailable as exc:
        raise PaymentError("UPI is temporarily unavailable. Please try again in a minute or pay a parking attendant.") from exc
    intent.order_id, intent.upi_uri, intent.qr_image_url = qr.order_id, qr.upi_uri, qr.image_url
    db.flush()
    return intent


def start_self_pay(db: Session, gw: PaymentGateway, entry: Entry, duration: int, *, qr_expiry_s: int,
                   phone: Optional[str] = None, plate_revealed: bool = False) -> PaymentIntent:
    if entry_is_paid(db, entry):
        raise PaymentError("already paid")
    amounts = self_pay_amounts(db, entry, duration)
    return _new_intent(db, gw, kind="SELF_PAY", plate=entry.plate, vehicle_class=entry.vehicle_class, amounts=amounts,
                       qr_expiry_s=qr_expiry_s, session_id=entry.session_id, duration=duration, phone=phone,
                       plate_revealed=plate_revealed, description=f"Parking {entry.masked_plate}")


def start_dues(db: Session, gw: PaymentGateway, vehicle: Vehicle, *, phone: str, qr_expiry_s: int) -> PaymentIntent:
    due = outstanding_dues(db, vehicle)
    if due <= 0:
        raise PaymentError("nothing to pay")
    return _new_intent(db, gw, kind="DUES", plate=vehicle.plate, vehicle_class=vehicle.vehicle_class,
                       amounts=Amounts(due, 0, due), qr_expiry_s=qr_expiry_s, phone=phone,
                       contact_verified=vehicle.phone_hash is None, plate_revealed=True,
                       description="Parking dues")


def start_pass(db: Session, gw: PaymentGateway, plate: str, pt: PassType, *, phone: str, qr_expiry_s: int,
               contact_verified: bool) -> PaymentIntent:
    if not pt.active:
        raise PaymentError("pass type not available")
    return _new_intent(db, gw, kind="PASS", plate=plate, vehicle_class=pt.vehicle_class,
                       amounts=Amounts(pt.price_paise, pt.price_paise, 0), qr_expiry_s=qr_expiry_s,
                       pass_type_id=pt.id, phone=phone, contact_verified=contact_verified, plate_revealed=True,
                       description=f"Parking pass {pt.name}"[:60])


# ------------------------------------------------------------------ confirmation
def _payload(intent: PaymentIntent) -> tuple[str, dict]:
    common = {"plate": intent.plate, "amount_paise": intent.amount_paise, "txn_ref": intent.txn_ref,
              "gateway_ref": intent.gateway_ref, "utr": intent.utr, "phone": intent.phone,
              "paid_at": intent.paid_at.isoformat() if intent.paid_at else None}
    if intent.kind == "SELF_PAY":
        return "SELF_PAY_PAID", {**common, "session_id": intent.session_id, "vehicle_class": intent.vehicle_class,
                                 "duration_minutes": intent.duration_minutes, "base_paise": intent.base_paise,
                                 "dues_paise": intent.dues_paise}
    if intent.kind == "DUES":
        return "DUES_PAID", {**common, "dues_paise": intent.dues_paise}
    return "PASS_PAID", {**common, "vehicle_class": intent.vehicle_class, "pass_type_id": intent.pass_type_id}


def mark_paid(db: Session, intent: PaymentIntent, *, gateway_ref: Optional[str], utr: Optional[str],
              amount_paise: Optional[int], at: Optional[datetime] = None) -> list[Outbox]:
    """Idempotent. Returns the outbox messages created (empty if it was already paid)."""
    # claim the PENDING -> PAID transition atomically (webhook and status poll may race)
    res = db.execute(update(PaymentIntent).where(PaymentIntent.id == intent.id,
                                                 PaymentIntent.status.in_(["PENDING", "EXPIRED", "FAILED"]))
                     .values(status="PAID"))
    if not res.rowcount:
        db.refresh(intent)
        return []
    db.refresh(intent)
    intent.gateway_ref, intent.utr = gateway_ref or intent.gateway_ref, utr or intent.utr
    intent.paid_at = at or utcnow()
    if amount_paise is not None and int(amount_paise) != intent.amount_paise:
        paid = int(amount_paise)
        intent.note = f"gateway amount {paid} differs from expected {intent.amount_paise}"
        log.warning("payment intent %s: gateway amount differs from expected", intent.id)
        intent.amount_paise = paid
        intent.dues_paise = min(intent.dues_paise, paid)
        intent.base_paise = paid - intent.dues_paise
    kind, payload = _payload(intent)
    msgs = [enqueue(db, kind, payload)]
    if intent.queue_contact_verified and intent.phone:
        msgs.append(enqueue(db, "CONTACT_VERIFIED", {"plate": intent.plate, "phone": intent.phone}))
    if intent.kind == "SELF_PAY" and intent.session_id:
        entry = db.get(Entry, intent.session_id)
        if entry is not None:
            entry.relay_paid_at = intent.paid_at
    db.flush()
    return msgs


def apply_gateway_event(db: Session, ev: WebhookEvent) -> tuple[Optional[PaymentIntent], list[Outbox]]:
    intent = None
    if ev.order_id:
        intent = db.scalars(select(PaymentIntent).where(PaymentIntent.order_id == ev.order_id)).first()
    if intent is None and ev.txn_ref:
        intent = db.scalars(select(PaymentIntent).where(PaymentIntent.txn_ref == ev.txn_ref)).first()
    if intent is None:
        return None, []  # not ours (e.g. an edge QR on a shared gateway account)
    if ev.status == "PAID":
        return intent, mark_paid(db, intent, gateway_ref=ev.gateway_ref, utr=ev.utr, amount_paise=ev.amount_paise)
    if ev.status == "FAILED" and intent.status == "PENDING":
        intent.status = "FAILED"
    return intent, []


def poll_status(db: Session, gw: PaymentGateway, intent: PaymentIntent, min_interval_s: float) -> list[Outbox]:
    """Ask the gateway (throttled). Also expires stale QRs."""
    if intent.status != "PENDING" or not intent.order_id:
        return []
    now = utcnow()
    if intent.last_polled_at and (now - intent.last_polled_at).total_seconds() < min_interval_s:
        return []
    intent.last_polled_at = now
    try:
        st: StatusResult = gw.fetch_status(intent.order_id, intent.txn_ref)
    except GatewayUnavailable:
        return []
    if st.status == "PAID":
        return mark_paid(db, intent, gateway_ref=st.gateway_ref, utr=st.utr, amount_paise=st.amount_paise)
    if st.status == "FAILED":
        intent.status = "FAILED"
    elif now > intent.expires_at + timedelta(minutes=2):
        intent.status = "EXPIRED"  # a late webhook can still move it to PAID
    return []


def receipt_for(db: Session, intent: PaymentIntent) -> Optional[Receipt]:
    if intent.status != "PAID":
        return None
    return db.scalars(select(Receipt).where(Receipt.txn_ref == intent.txn_ref)).first()


def purge_phones(db: Session, days: int) -> int:
    """Data minimisation: drop phone numbers from old payment intents (already delivered to the edge)."""
    cutoff = utcnow() - timedelta(days=days)
    res = db.execute(update(PaymentIntent).where(PaymentIntent.created_at < cutoff, PaymentIntent.phone.is_not(None))
                     .values(phone=None))
    return res.rowcount or 0
