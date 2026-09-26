"""Payments: UPI (online dynamic QR / offline intent QR) and the controlled cash pathway.

Invariants enforced here (spec 5, 5A, 12):
* amounts are always computed by the tariff engine (plus previous dues); a different amount
  needs an Override approved by a supervisor PIN;
* every payment is tied to a vehicle and a session or pass;
* cash is CONFIRMED on record, requires an open shift, respects the cash-in-hand limit and
  produces a digital receipt immediately;
* UPI is CONFIRMED only by gateway webhook/status check or reconciliation;
* only supervisors reverse cash / refund UPI, with a reason; everything posts to the ledger.
"""
from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import events
from ..adapters.gateway import GatewayUnavailable, SettlementLine, get_gateway, upi_intent_uri
from ..db import utcnow
from ..models import (
    LedgerKind,
    Override,
    ParkingSession,
    Pass,
    PassType,
    Payment,
    PayMode,
    PayStatus,
    Role,
    SessionStatus,
    User,
    Vehicle,
)
from . import cash, ledger
from .lookup import get_tariff, site_tz
from .settings import get_setting
from .tariff import calculate_charge


class PaymentError(ValueError):
    """Business-rule violation; message is shown to the worker."""


class CashLimitReached(PaymentError):
    pass


# ------------------------------------------------------------------ amounts
@dataclass
class Quote:
    vehicle_id: int
    session_id: Optional[int]
    base_paise: int
    dues_paise: int
    credit_paise: int
    amount_paise: int
    duration_minutes: Optional[int] = None
    pass_type_id: Optional[int] = None

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def quote_session(db: Session, session_id: int, duration_minutes: int) -> Quote:
    sess = db.get(ParkingSession, session_id)
    if sess is None:
        raise PaymentError("session not found")
    if sess.status not in (SessionStatus.OPEN, SessionStatus.PREPAID):
        raise PaymentError(f"session is {sess.status}; nothing to collect")
    if duration_minutes <= 0 or duration_minutes > 7 * 1440:
        raise PaymentError("invalid duration")
    t = get_tariff(db, sess.vehicle_class, sess.entry_at)
    base = calculate_charge(sess.vehicle_class, sess.entry_at, sess.entry_at + timedelta(minutes=duration_minutes), t, site_tz())
    veh = db.get(Vehicle, sess.vehicle_id)
    bal = veh.balance_paise  # includes credit from payments already made for this session
    return Quote(veh.id, sess.id, base, max(0, bal), max(0, -bal), max(0, base + bal), duration_minutes)


def quote_dues(db: Session, vehicle_id: int) -> Quote:
    veh = db.get(Vehicle, vehicle_id)
    if veh is None:
        raise PaymentError("vehicle not found")
    if veh.balance_paise <= 0:
        raise PaymentError("no dues")
    return Quote(veh.id, None, 0, veh.balance_paise, 0, veh.balance_paise)


def quote_pass(db: Session, vehicle_id: int, pass_type_id: int, include_dues: bool = True) -> Quote:
    veh = db.get(Vehicle, vehicle_id)
    pt = db.get(PassType, pass_type_id)
    if veh is None or pt is None or not pt.active:
        raise PaymentError("vehicle or pass type not found")
    if pt.vehicle_class != veh.vehicle_class:
        raise PaymentError("pass type is for a different vehicle class")
    dues = max(0, veh.balance_paise) if include_dues else 0
    return Quote(veh.id, None, pt.price_paise, dues, 0, pt.price_paise + dues, pass_type_id=pt.id)


# ------------------------------------------------------------------ helpers
def _b36(n: int) -> str:
    chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    out = ""
    while True:
        n, r = divmod(n, 36)
        out = chars[r] + out
        if n == 0:
            return out


def make_txn_ref(kind: str, ident: int) -> str:
    """UPI `tr` reference encoding what is being paid: PS<session>, PP<pass>, PV<vehicle>."""
    return f"P{kind}{_b36(ident)}X{secrets.token_hex(3).upper()}"


_REF_RE = re.compile(r"^P([SPV])([0-9A-Z]+)X[0-9A-F]+$")


def parse_txn_ref(ref: str) -> Optional[tuple[str, int]]:
    m = _REF_RE.match(ref or "")
    return (m.group(1), int(m.group(2), 36)) if m else None


def verify_supervisor_pin(db: Session, pin: Optional[str]) -> User:
    from ..security import verify_secret

    if not pin:
        raise PaymentError("supervisor PIN required")
    for u in db.scalars(select(User).where(User.role.in_([Role.SUPERVISOR, Role.ADMIN]), User.active.is_(True))).all():
        if u.pin_hash and verify_secret(pin, u.pin_hash):
            return u
    raise PaymentError("invalid supervisor PIN")


def _apply_override(db: Session, q: Quote, override_paise: Optional[int], reason: Optional[str],
                    supervisor_pin: Optional[str], requested_by: Optional[int]) -> Optional[Override]:
    if override_paise is None or override_paise == q.amount_paise:
        return None
    if override_paise < 0:
        raise PaymentError("invalid override amount")
    if not reason or not reason.strip():
        raise PaymentError("override needs a reason")
    sup = verify_supervisor_pin(db, supervisor_pin)
    ov = Override(kind="AMOUNT", session_id=q.session_id, vehicle_id=q.vehicle_id, original_paise=q.amount_paise,
                  new_paise=override_paise, reason=reason, requested_by=requested_by, approved_by=sup.id)
    db.add(ov)
    db.flush()
    q.amount_paise = override_paise
    return ov


def _new_payment(db: Session, q: Quote, mode: str, *, user: Optional[User], channel: str, purpose: str,
                 pass_id: Optional[int] = None, phone: Optional[str] = None, client_uuid: Optional[str] = None,
                 client_created_at: Optional[datetime] = None, override: Optional[Override] = None,
                 parked_location: Optional[str] = None) -> Payment:
    if q.session_id:
        ref = make_txn_ref("S", q.session_id)
    elif pass_id:
        ref = make_txn_ref("P", pass_id)
    else:
        ref = make_txn_ref("V", q.vehicle_id)
    sess = db.get(ParkingSession, q.session_id) if q.session_id else None
    shift = cash.current_shift(db, user.id) if user is not None and user.role != Role.ADMIN else None
    if sess is not None and parked_location:
        sess.parked_location = parked_location
    p = Payment(vehicle_id=q.vehicle_id, session_id=q.session_id, pass_id=pass_id, purpose=purpose, mode=mode,
                amount_paise=q.amount_paise, base_paise=q.base_paise, dues_paise=q.dues_paise,
                duration_minutes=q.duration_minutes, collected_by=user.id if user else None, channel=channel,
                shift_id=shift.id if shift else None, zone_id=(shift.zone_id if shift and shift.zone_id else (sess.zone_id if sess else None)),
                txn_ref=ref, status=PayStatus.INITIATED, phone=phone, client_uuid=client_uuid,
                client_created_at=client_created_at, override_id=override.id if override else None)
    db.add(p)
    db.flush()
    if override is not None:
        override.payment_id = p.id
    if phone:
        veh = db.get(Vehicle, q.vehicle_id)
        if not veh.phone:
            veh.phone = phone
    if sess is not None and duration_is_set(q):
        sess.est_duration_minutes = q.duration_minutes
    return p


def duration_is_set(q: Quote) -> bool:
    return q.duration_minutes is not None


def _check_payable(q: Quote) -> None:
    if q.amount_paise <= 0:
        raise PaymentError("nothing to pay")


# ------------------------------------------------------------------ pass sales
def create_pending_pass(db: Session, vehicle_id: int, pass_type_id: int, *, user_id: Optional[int], channel: str,
                        start: Optional[datetime] = None) -> Pass:
    from .passes import pass_window

    veh = db.get(Vehicle, vehicle_id)
    pt = db.get(PassType, pass_type_id)
    starts, ends = pass_window(db, veh.id, pt, start or utcnow())
    p = Pass(vehicle_id=veh.id, vehicle_class=veh.vehicle_class, pass_type_id=pt.id, starts_at=starts, ends_at=ends,
             amount_paise=pt.price_paise, status="PENDING", sold_by=user_id, channel=channel)
    db.add(p)
    db.flush()
    return p


# ------------------------------------------------------------------ UPI
def start_upi(db: Session, q: Quote, *, user: Optional[User], channel: str = "WORKER", purpose: str = "SESSION",
              pass_id: Optional[int] = None, phone: Optional[str] = None, override_paise: Optional[int] = None,
              override_reason: Optional[str] = None, supervisor_pin: Optional[str] = None,
              parked_location: Optional[str] = None, client_uuid: Optional[str] = None) -> Payment:
    """Create a UPI payment. Online: dynamic gateway QR. Gateway down: offline intent QR."""
    if client_uuid:
        existing = db.scalars(select(Payment).where(Payment.client_uuid == client_uuid)).first()
        if existing is not None:
            return existing
    ov = _apply_override(db, q, override_paise, override_reason, supervisor_pin, user.id if user else None)
    _check_payable(q)
    p = _new_payment(db, q, PayMode.UPI, user=user, channel=channel, purpose=purpose, pass_id=pass_id, phone=phone,
                     override=ov, parked_location=parked_location, client_uuid=client_uuid)
    veh = db.get(Vehicle, q.vehicle_id)
    desc = f"Parking {veh.plate}"
    try:
        qr = get_gateway().create_upi_qr(p.amount_paise, p.txn_ref, desc)
        p.gateway_order_id, p.upi_uri = qr.order_id, qr.upi_uri
    except GatewayUnavailable:
        p.offline = True
        p.upi_uri = upi_intent_uri(get_setting(db, "upi_vpa"), get_setting(db, "upi_payee_name"), p.amount_paise, p.txn_ref, desc)
    db.flush()
    return p


def claim_offline(db: Session, payment: Payment, user: Optional[User]) -> Payment:
    """Worker confirms the customer paid an offline intent QR (their success screen)."""
    if payment.status != PayStatus.INITIATED:
        return payment
    payment.status = PayStatus.CLAIMED_OFFLINE
    payment.offline = True
    _after_claim(db, payment)
    return payment


def record_offline_claim(db: Session, *, user: User, client_uuid: str, amount_paise: int, txn_ref: str,
                         client_created_at: datetime, session_id: Optional[int] = None, vehicle_id: Optional[int] = None,
                         duration_minutes: Optional[int] = None, dues_paise: int = 0, phone: Optional[str] = None,
                         pass_type_id: Optional[int] = None) -> Payment:
    """A UPI claim created on the phone while the server was unreachable (synced later, idempotent)."""
    existing = db.scalars(select(Payment).where(Payment.client_uuid == client_uuid)).first()
    if existing is not None:
        return existing
    if db.scalars(select(Payment).where(Payment.txn_ref == txn_ref)).first() is not None:
        txn_ref = f"{txn_ref}-{secrets.token_hex(2).upper()}"
    q, purpose, pass_id = _offline_quote(db, session_id, vehicle_id, duration_minutes, dues_paise, amount_paise, pass_type_id,
                                         user, client_created_at)
    shift = cash.current_shift(db, user.id, at=client_created_at)
    p = Payment(vehicle_id=q.vehicle_id, session_id=q.session_id, pass_id=pass_id, purpose=purpose, mode=PayMode.UPI,
                amount_paise=amount_paise, base_paise=q.base_paise, dues_paise=q.dues_paise, duration_minutes=duration_minutes,
                collected_by=user.id, channel="WORKER", shift_id=shift.id if shift else None,
                zone_id=shift.zone_id if shift else None, txn_ref=txn_ref, status=PayStatus.CLAIMED_OFFLINE, offline=True,
                client_uuid=client_uuid, client_created_at=client_created_at, phone=phone, created_at=client_created_at)
    db.add(p)
    db.flush()
    _after_claim(db, p)
    return p


def _offline_quote(db, session_id, vehicle_id, duration_minutes, dues_paise, amount_paise, pass_type_id, user, at):
    """Rebuild and validate the device-side quote: base must equal the tariff engine's number."""
    pass_id = None
    if pass_type_id:
        vid = vehicle_id or db.get(ParkingSession, session_id).vehicle_id
        q = quote_pass(db, vid, pass_type_id, include_dues=False)
        p = create_pending_pass(db, vid, pass_type_id, user_id=user.id, channel="WORKER", start=at)
        pass_id, purpose = p.id, "PASS"
        q.session_id = None
    elif session_id:
        sess = db.get(ParkingSession, session_id)
        if sess is None:
            raise PaymentError("session not found")
        t = get_tariff(db, sess.vehicle_class, sess.entry_at)
        base = calculate_charge(sess.vehicle_class, sess.entry_at, sess.entry_at + timedelta(minutes=duration_minutes or 120), t, site_tz())
        q = Quote(sess.vehicle_id, sess.id, base, 0, 0, 0, duration_minutes)
        purpose = "SESSION"
    elif vehicle_id:
        q = Quote(vehicle_id, None, 0, 0, 0, 0)
        purpose = "DUES"
    else:
        raise PaymentError("session_id or vehicle_id required")
    if dues_paise < 0 or amount_paise != max(0, q.base_paise + dues_paise):
        # device may have applied a credit; accept only amounts not above tariff+declared dues
        if amount_paise > q.base_paise + max(0, dues_paise) or amount_paise <= 0:
            raise PaymentError("amount does not match tariff")
    q.dues_paise = max(0, dues_paise)
    q.amount_paise = amount_paise
    return q, purpose, pass_id


def _after_claim(db: Session, p: Payment) -> None:
    sess = db.get(ParkingSession, p.session_id) if p.session_id else None
    if sess is not None and sess.status == SessionStatus.OPEN:
        sess.status = SessionStatus.PREPAID
    db.flush()
    events.emit(db, "payment.updated", payment_dict(db, p))


def confirm_payment(db: Session, p: Payment, *, gateway_ref: Optional[str] = None, utr: Optional[str] = None,
                    amount_paise: Optional[int] = None, at: Optional[datetime] = None, note: Optional[str] = None) -> Payment:
    """Idempotently move a payment to CONFIRMED, post the ledger PAYMENT and issue the receipt."""
    if p.status == PayStatus.CONFIRMED:
        return p
    if p.status not in (PayStatus.INITIATED, PayStatus.CLAIMED_OFFLINE, PayStatus.FAILED):
        raise PaymentError(f"cannot confirm a {p.status} payment")
    if amount_paise is not None and amount_paise != p.amount_paise:
        p.status_note = f"gateway amount {amount_paise} differs from expected {p.amount_paise}"
        p.amount_paise = amount_paise
    p.status = PayStatus.CONFIRMED
    p.confirmed_at = at or utcnow()
    p.gateway_ref = gateway_ref or p.gateway_ref
    p.utr = utr or p.utr
    if note:
        p.status_note = ((p.status_note or "") + " " + note).strip()
    ledger.post(db, p.vehicle_id, LedgerKind.PAYMENT, -p.amount_paise, session_id=p.session_id, payment_id=p.id,
                pass_id=p.pass_id, user_id=p.collected_by)
    if p.pass_id:
        from .passes import activate_pass

        activate_pass(db, db.get(Pass, p.pass_id), p)
    sess = db.get(ParkingSession, p.session_id) if p.session_id else None
    if sess is not None:
        if sess.status == SessionStatus.OPEN:
            sess.status = SessionStatus.PREPAID
        elif sess.status == SessionStatus.CLOSED and db.get(Vehicle, p.vehicle_id).balance_paise <= 0:
            sess.status = SessionStatus.SETTLED
    from .receipts import issue_receipt

    issue_receipt(db, p)
    db.flush()
    events.emit(db, "payment.updated", payment_dict(db, p))
    return p


def fail_payment(db: Session, p: Payment, note: str) -> Payment:
    if p.status in (PayStatus.INITIATED, PayStatus.CLAIMED_OFFLINE):
        was_claimed = p.status == PayStatus.CLAIMED_OFFLINE
        p.status = PayStatus.FAILED
        p.status_note = note
        sess = db.get(ParkingSession, p.session_id) if p.session_id else None
        if was_claimed and sess is not None and sess.status == SessionStatus.PREPAID:
            other = db.scalar(select(func.count(Payment.id)).where(
                Payment.session_id == sess.id, Payment.id != p.id,
                Payment.status.in_([PayStatus.CONFIRMED, PayStatus.CLAIMED_OFFLINE])))
            if not other:
                sess.status = SessionStatus.OPEN
        db.flush()
        events.emit(db, "payment.updated", payment_dict(db, p))
    return p


def check_upi_status(db: Session, p: Payment) -> Payment:
    if p.status != PayStatus.INITIATED or p.offline or not p.gateway_order_id:
        return p
    st = get_gateway().fetch_status(p.gateway_order_id, p.txn_ref)
    if st.status == "PAID":
        confirm_payment(db, p, gateway_ref=st.gateway_ref, utr=st.utr, amount_paise=st.amount_paise)
    elif st.status == "FAILED":
        fail_payment(db, p, "gateway reported failure")
    return p


def handle_webhook(db: Session, body: bytes, headers: dict[str, str]) -> list[Payment]:
    out = []
    for wev in get_gateway().parse_webhook(body, headers):
        p = None
        if wev.order_id:
            p = db.scalars(select(Payment).where(Payment.gateway_order_id == wev.order_id)).first()
        if p is None and wev.txn_ref:
            p = db.scalars(select(Payment).where(Payment.txn_ref == wev.txn_ref)).first()
        if p is None:
            continue
        if wev.status == "PAID":
            confirm_payment(db, p, gateway_ref=wev.gateway_ref, utr=wev.utr, amount_paise=wev.amount_paise)
        elif wev.status == "FAILED":
            fail_payment(db, p, "gateway webhook: failed")
        out.append(p)
    return out


# ------------------------------------------------------------------ cash
def cash_allowed_for(db: Session, user: User) -> None:
    if not get_setting(db, "cash_enabled"):
        raise PaymentError("cash is switched off; use UPI")
    if get_setting(db, "cash_desk_only") and user.id not in (get_setting(db, "cash_desk_user_ids") or []):
        raise PaymentError("cash only at the cash desk")


def record_cash(db: Session, q: Quote, *, user: User, purpose: str = "SESSION", pass_id: Optional[int] = None,
                phone: Optional[str] = None, override_paise: Optional[int] = None, override_reason: Optional[str] = None,
                supervisor_pin: Optional[str] = None, client_uuid: Optional[str] = None,
                client_created_at: Optional[datetime] = None, offline_sync: bool = False,
                parked_location: Optional[str] = None) -> Payment:
    """Worker confirms 'Cash received ₹X' for the system-calculated amount."""
    if client_uuid:
        existing = db.scalars(select(Payment).where(Payment.client_uuid == client_uuid)).first()
        if existing is not None:
            return existing
    cash_allowed_for(db, user)
    ov = _apply_override(db, q, override_paise, override_reason, supervisor_pin, user.id)
    _check_payable(q)
    at = client_created_at or utcnow()
    shift = cash.current_shift(db, user.id, at=at) or cash.open_shift(db, user, at=at)
    limit = int(get_setting(db, "cash_limit_paise"))
    held = cash.cash_in_hand(db, user.id, shift.id)
    breach = held + q.amount_paise > limit
    if breach and not offline_sync:
        raise CashLimitReached(f"cash-in-hand limit ₹{limit // 100} reached; hand over cash first (UPI still works)")
    p = _new_payment(db, q, PayMode.CASH, user=user, channel="WORKER", purpose=purpose, pass_id=pass_id, phone=phone,
                     client_uuid=client_uuid, client_created_at=client_created_at, override=ov, parked_location=parked_location)
    p.shift_id = shift.id
    p.limit_breach = breach
    if client_created_at:
        p.created_at = client_created_at
    confirm_payment(db, p, at=at)
    events.emit(db, "cash.updated", cash.holding_dict(db, user.id))
    return p


# ------------------------------------------------------------------ supervisor actions
def reverse_cash(db: Session, payment_id: int, *, supervisor: User, reason: str) -> Payment:
    if supervisor.role not in (Role.SUPERVISOR, Role.ADMIN):
        raise PermissionError("supervisor only")
    if not reason or not reason.strip():
        raise PaymentError("reason required")
    p = db.get(Payment, payment_id)
    if p is None or p.mode != PayMode.CASH or p.status != PayStatus.CONFIRMED:
        raise PaymentError("only confirmed cash payments can be reversed")
    p.status = PayStatus.REVERSED
    p.status_note = reason
    db.add(Override(kind="REVERSAL", payment_id=p.id, session_id=p.session_id, vehicle_id=p.vehicle_id,
                    original_paise=p.amount_paise, new_paise=0, reason=reason, approved_by=supervisor.id))
    ledger.post(db, p.vehicle_id, LedgerKind.REVERSAL, p.amount_paise, session_id=p.session_id, payment_id=p.id,
                reason=reason, user_id=supervisor.id)
    db.flush()
    events.emit(db, "payment.updated", payment_dict(db, p))
    return p


def refund_upi(db: Session, payment_id: int, *, supervisor: User, reason: str, amount_paise: Optional[int] = None) -> Payment:
    if supervisor.role not in (Role.SUPERVISOR, Role.ADMIN):
        raise PermissionError("supervisor only")
    if not reason or not reason.strip():
        raise PaymentError("reason required")
    p = db.get(Payment, payment_id)
    if p is None or p.mode != PayMode.UPI or p.status != PayStatus.CONFIRMED:
        raise PaymentError("only confirmed UPI payments can be refunded")
    amt = amount_paise or p.amount_paise
    if amt <= 0 or amt > p.amount_paise:
        raise PaymentError("invalid refund amount")
    if not p.gateway_ref:
        raise PaymentError("payment has no gateway reference to refund against")
    res = get_gateway().refund(p.gateway_ref, amt, reason)
    if res.status == "FAILED":
        raise PaymentError("gateway refused the refund")
    if amt == p.amount_paise:
        p.status = PayStatus.REFUNDED
    p.status_note = f"refund {res.refund_id} ₹{amt / 100:.2f}: {reason}"
    db.add(Override(kind="REFUND", payment_id=p.id, session_id=p.session_id, vehicle_id=p.vehicle_id,
                    original_paise=p.amount_paise, new_paise=p.amount_paise - amt, reason=reason, approved_by=supervisor.id))
    ledger.post(db, p.vehicle_id, LedgerKind.REFUND, amt, session_id=p.session_id, payment_id=p.id,
                reason=reason, user_id=supervisor.id)
    db.flush()
    return p


def adjust_balance(db: Session, vehicle_id: int, amount_paise: int, *, supervisor: User, reason: str) -> None:
    if supervisor.role not in (Role.SUPERVISOR, Role.ADMIN):
        raise PermissionError("supervisor only")
    if not reason or not reason.strip():
        raise PaymentError("reason required")
    veh = db.get(Vehicle, vehicle_id)
    db.add(Override(kind="ADJUSTMENT", vehicle_id=vehicle_id, original_paise=veh.balance_paise,
                    new_paise=veh.balance_paise + amount_paise, reason=reason, approved_by=supervisor.id))
    ledger.post(db, vehicle_id, LedgerKind.ADJUSTMENT, amount_paise, reason=reason, user_id=supervisor.id)


# ------------------------------------------------------------------ reconciliation
def reconcile_upi(db: Session, lines: list[SettlementLine], day: Optional[str] = None) -> dict:
    """Match gateway/bank lines to payments by txn_ref. Confirms offline claims; reports mismatches."""
    by_ref = {}
    for ln in lines:
        if ln.txn_ref:
            by_ref.setdefault(ln.txn_ref, []).append(ln)
    confirmed, amount_mismatch, unknown = [], [], []
    for ref, lns in by_ref.items():
        p = db.scalars(select(Payment).where(Payment.txn_ref == ref)).first()
        total = sum(line.amount_paise for line in lns)
        if p is None:
            parsed = parse_txn_ref(ref)
            unknown.append({"txn_ref": ref, "amount_paise": total, "parsed": parsed})
            continue
        if p.status in (PayStatus.CLAIMED_OFFLINE, PayStatus.INITIATED, PayStatus.FAILED):
            confirm_payment(db, p, gateway_ref=lns[0].gateway_ref, utr=lns[0].utr, amount_paise=total,
                            note="reconciled from settlement")
            confirmed.append(p.id)
        elif p.status == PayStatus.CONFIRMED and p.amount_paise != total:
            amount_mismatch.append({"payment_id": p.id, "txn_ref": ref, "system_paise": p.amount_paise, "settled_paise": total})
    # confirmed UPI payments that never appear in the settlement
    missing = []
    refs = set(by_ref)
    stmt = select(Payment).where(Payment.mode == PayMode.UPI, Payment.status == PayStatus.CONFIRMED)
    if day:
        from .lookup import day_bounds

        start, end = day_bounds(day)
        stmt = stmt.where(Payment.created_at >= start, Payment.created_at < end)
    for p in db.scalars(stmt).all():
        if p.txn_ref not in refs:
            missing.append({"payment_id": p.id, "txn_ref": p.txn_ref, "amount_paise": p.amount_paise})
    db.flush()
    return {"confirmed_offline": confirmed, "amount_mismatch": amount_mismatch, "unknown_credits": unknown,
            "missing_from_settlement": missing}


def stale_offline_claims(db: Session, now: Optional[datetime] = None) -> list[Payment]:
    hours = int(get_setting(db, "offline_claim_review_hours"))
    cutoff = (now or utcnow()) - timedelta(hours=hours)
    return list(db.scalars(select(Payment).where(Payment.status == PayStatus.CLAIMED_OFFLINE,
                                                 Payment.created_at < cutoff)).all())


# ------------------------------------------------------------------ serialisation
def payment_dict(db: Session, p: Payment) -> dict:
    veh = db.get(Vehicle, p.vehicle_id)
    from ..models import Receipt

    rec = db.get(Receipt, p.receipt_id) if p.receipt_id else None
    return {"id": p.id, "vehicle_id": p.vehicle_id, "plate": veh.plate if veh else None, "session_id": p.session_id,
            "pass_id": p.pass_id, "purpose": p.purpose, "mode": p.mode, "amount_paise": p.amount_paise,
            "base_paise": p.base_paise, "dues_paise": p.dues_paise, "duration_minutes": p.duration_minutes,
            "status": p.status, "txn_ref": p.txn_ref, "upi_uri": p.upi_uri, "offline": p.offline,
            "collected_by": p.collected_by, "channel": p.channel, "created_at": p.created_at.isoformat() if p.created_at else None,
            "confirmed_at": p.confirmed_at.isoformat() if p.confirmed_at else None, "utr": p.utr,
            "receipt": {"code": rec.code, "number": rec.number, "link": rec.data.get("link"), "channel": rec.channel,
                        "delivery_status": rec.delivery_status} if rec else None,
            "limit_breach": p.limit_breach, "status_note": p.status_note}
