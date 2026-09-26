"""ORM models. Money is always integer paise. Timestamps are timezone-aware UTC.

Financial tables (ledger_entries, payments, cash_handovers, bank_deposits, overrides,
receipts, passes) are append-only: rows are never deleted (enforced in audit.py) and
corrections are made through new rows (ADJUSTMENT ledger entries, reversals).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, UTCDateTime, utcnow


# ---------------------------------------------------------------- enums (string codes)
class Role:
    ADMIN = "ADMIN"
    SUPERVISOR = "SUPERVISOR"
    WORKER = "WORKER"
    GUARD = "GUARD"
    ALL = (ADMIN, SUPERVISOR, WORKER, GUARD)


class EventStatus:
    MATCHED = "MATCHED"
    UNREAD = "UNREAD"
    REVIEW = "REVIEW"
    WRONG_WAY = "WRONG_WAY"
    DUPLICATE = "DUPLICATE"
    MERGED = "MERGED"
    RESOLVED = "RESOLVED"  # review item resolved manually (kept distinct for accuracy stats)
    DISCARDED = "DISCARDED"


class SessionStatus:
    OPEN = "OPEN"
    PREPAID = "PREPAID"
    PASS = "PASS"
    CLOSED = "CLOSED"
    SETTLED = "SETTLED"
    ORPHAN_EXIT = "ORPHAN_EXIT"
    ORPHAN_ENTRY = "ORPHAN_ENTRY"
    ACTIVE = (OPEN, PREPAID, PASS)


class LedgerKind:
    CHARGE = "CHARGE"
    PAYMENT = "PAYMENT"
    ADJUSTMENT = "ADJUSTMENT"
    PASS_SALE = "PASS_SALE"
    REFUND = "REFUND"  # money returned to customer (positive: restores what they owe)
    REVERSAL = "REVERSAL"  # reversed payment (positive)


class PayMode:
    UPI = "UPI"
    CASH = "CASH"


class PayStatus:
    INITIATED = "INITIATED"
    CLAIMED_OFFLINE = "CLAIMED_OFFLINE"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"
    REVERSED = "REVERSED"


class Direction:
    IN = "IN"
    OUT = "OUT"
    BOTH = "BOTH"


# ---------------------------------------------------------------- configuration
class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class VehicleClass(Base):
    __tablename__ = "vehicle_classes"
    code: Mapped[str] = mapped_column(String(16), primary_key=True)  # BIKE, CAR
    name: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Tariff(Base):
    """Versioned tariff per vehicle class. Never edited once used: a change is a new version."""

    __tablename__ = "tariffs"
    __table_args__ = (UniqueConstraint("vehicle_class", "version"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vehicle_class: Mapped[str] = mapped_column(ForeignKey("vehicle_classes.code"))
    version: Mapped[int] = mapped_column(Integer)
    effective_from: Mapped[datetime] = mapped_column(UTCDateTime)
    first_slab_minutes: Mapped[int] = mapped_column(Integer, default=120)
    first_slab_paise: Mapped[int] = mapped_column(Integer, default=1000)
    per_hour_paise: Mapped[int] = mapped_column(Integer, default=500)
    grace_minutes: Mapped[int] = mapped_column(Integer, default=10)
    block_minutes: Mapped[int] = mapped_column(Integer, default=720)
    block_cap_paise: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=3000)
    daily_cap_paise: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    overnight_paise: Mapped[int] = mapped_column(Integer, default=0)
    overnight_cutoff_hour: Mapped[int] = mapped_column(Integer, default=0)  # local hour
    free_minutes: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class PassType(Base):
    __tablename__ = "pass_types"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vehicle_class: Mapped[str] = mapped_column(ForeignKey("vehicle_classes.code"))
    name: Mapped[str] = mapped_column(String(64))
    period_unit: Mapped[str] = mapped_column(String(8), default="MONTH")  # MONTH | DAY
    period_value: Mapped[int] = mapped_column(Integer, default=1)
    price_paise: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)


class Gate(Base):
    __tablename__ = "gates"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    direction: Mapped[str] = mapped_column(String(8), default=Direction.BOTH)
    # [{"days":[0..6] optional, "from":"06:00", "to":"12:00", "direction":"IN"}] local time
    schedule: Mapped[list] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    cameras: Mapped[list["Camera"]] = relationship(back_populates="gate", order_by="Camera.id")


class Camera(Base):
    __tablename__ = "cameras"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    gate_id: Mapped[str] = mapped_column(ForeignKey("gates.id"))
    role: Mapped[str] = mapped_column(String(16), default="ANPR")  # ANPR | OVERVIEW
    side: Mapped[str] = mapped_column(String(8), default="LEFT")  # LEFT | RIGHT | CENTER
    rtsp_url: Mapped[str] = mapped_column(String(512), default="")
    roi: Mapped[list] = mapped_column(JSON, default=list)
    capture_line: Mapped[list] = mapped_column(JSON, default=list)
    in_vector: Mapped[list] = mapped_column(JSON, default=lambda: [0, 1])
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    gate: Mapped[Gate] = relationship(back_populates="cameras")


class Zone(Base):
    __tablename__ = "zones"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    gate_id: Mapped[Optional[str]] = mapped_column(ForeignKey("gates.id"), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(16))
    pin_hash: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    password_hash: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    device_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ZoneAssignment(Base):
    """worker_zones: which worker covers which zone during which time window (a shift)."""

    __tablename__ = "worker_zones"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    zone_id: Mapped[int] = mapped_column(ForeignKey("zones.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime)
    ends_at: Mapped[datetime] = mapped_column(UTCDateTime)
    shift_label: Mapped[str] = mapped_column(String(32), default="")
    assigned_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    __table_args__ = (Index("ix_wz_zone_time", "zone_id", "starts_at", "ends_at"),)


class Shift(Base):
    __tablename__ = "shifts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    zone_id: Mapped[Optional[int]] = mapped_column(ForeignKey("zones.id"), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    closed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    opening_cash_paise: Mapped[int] = mapped_column(Integer, default=0)  # always zero
    upi_total_paise: Mapped[int] = mapped_column(Integer, default=0)
    cash_total_paise: Mapped[int] = mapped_column(Integer, default=0)
    handed_over_paise: Mapped[int] = mapped_column(Integer, default=0)
    variance_paise: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(8), default="OPEN")
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


# ---------------------------------------------------------------- vehicles & events
class Vehicle(Base):
    __tablename__ = "vehicles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plate: Mapped[str] = mapped_column(String(16), unique=True)  # normalised
    plate_canon: Mapped[str] = mapped_column(String(16), index=True, default="")  # confusion-collapsed
    display_plate: Mapped[str] = mapped_column(String(24))
    vehicle_class: Mapped[str] = mapped_column(ForeignKey("vehicle_classes.code"))
    first_seen: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    phone: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # denormalised cache of sum(ledger) for fast lookups; ledger stays the source of truth
    balance_paise: Mapped[int] = mapped_column(Integer, default=0, index=True)


class AnprEvent(Base):
    __tablename__ = "anpr_events"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    gate_id: Mapped[str] = mapped_column(ForeignKey("gates.id"))
    camera_ids: Mapped[list] = mapped_column(JSON, default=list)
    direction: Mapped[str] = mapped_column(String(4))
    wrong_way: Mapped[bool] = mapped_column(Boolean, default=False)
    vehicle_class: Mapped[str] = mapped_column(String(16), default="BIKE")
    ts: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    raw_plate: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    plate_norm: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, index=True)
    confidence: Mapped[float] = mapped_column(default=0.0)
    candidates: Mapped[list] = mapped_column(JSON, default=list)
    images: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), index=True)
    review_reason: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    vehicle_id: Mapped[Optional[int]] = mapped_column(ForeignKey("vehicles.id"), nullable=True)
    session_id: Mapped[Optional[int]] = mapped_column(ForeignKey("parking_sessions.id"), nullable=True)
    match_type: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)  # EXACT/APPROX/MANUAL
    match_distance: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    matched_plate: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    reviewed_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    review_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    __table_args__ = (Index("ix_evt_gate_plate_ts", "gate_id", "plate_norm", "ts"),)


class PlateCorrection(Base):
    """Every approximate match / manual correction: raw read vs chosen plate (ANPR accuracy data)."""

    __tablename__ = "plate_corrections"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    session_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    camera_ids: Mapped[list] = mapped_column(JSON, default=list)
    raw_plate: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    chosen_plate: Mapped[str] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(24))  # APPROX_AUTO | WORKER | REVIEW
    distance: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ParkingSession(Base):
    __tablename__ = "parking_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vehicle_id: Mapped[Optional[int]] = mapped_column(ForeignKey("vehicles.id"), nullable=True, index=True)
    vehicle_class: Mapped[str] = mapped_column(String(16), default="BIKE")
    entry_event_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    exit_event_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    entry_gate: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    exit_gate: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    entry_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    exit_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    est_duration_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    zone_id: Mapped[Optional[int]] = mapped_column(ForeignKey("zones.id"), nullable=True)
    parked_location: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    pass_id: Mapped[Optional[int]] = mapped_column(ForeignKey("passes.id"), nullable=True)
    tariff_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tariffs.id"), nullable=True)
    charge_paise: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    entry_match: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    exit_match: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    unpaid_flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    closed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    vehicle: Mapped[Optional[Vehicle]] = relationship()


# ---------------------------------------------------------------- money
class LedgerEntry(Base):
    """Append-only. Positive amount = customer owes more; negative = customer paid / credit."""

    __tablename__ = "ledger_entries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), index=True)
    session_id: Mapped[Optional[int]] = mapped_column(ForeignKey("parking_sessions.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(16))
    amount_paise: Mapped[int] = mapped_column(Integer)
    payment_id: Mapped[Optional[int]] = mapped_column(ForeignKey("payments.id"), nullable=True)
    pass_id: Mapped[Optional[int]] = mapped_column(ForeignKey("passes.id"), nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), index=True)
    session_id: Mapped[Optional[int]] = mapped_column(ForeignKey("parking_sessions.id"), nullable=True, index=True)
    pass_id: Mapped[Optional[int]] = mapped_column(ForeignKey("passes.id"), nullable=True)
    purpose: Mapped[str] = mapped_column(String(16), default="SESSION")  # SESSION | PASS | DUES
    mode: Mapped[str] = mapped_column(String(8))
    amount_paise: Mapped[int] = mapped_column(Integer)
    base_paise: Mapped[int] = mapped_column(Integer, default=0)  # tariff estimate part
    dues_paise: Mapped[int] = mapped_column(Integer, default=0)  # previous dues cleared
    duration_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    collected_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    channel: Mapped[str] = mapped_column(String(16), default="WORKER")  # WORKER | SELF_PAY | ADMIN
    shift_id: Mapped[Optional[int]] = mapped_column(ForeignKey("shifts.id"), nullable=True, index=True)
    zone_id: Mapped[Optional[int]] = mapped_column(ForeignKey("zones.id"), nullable=True)
    txn_ref: Mapped[str] = mapped_column(String(40), unique=True)
    gateway_ref: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # gateway payment id
    gateway_order_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)  # QR id
    utr: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    upi_uri: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    offline: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), index=True)
    client_uuid: Mapped[Optional[str]] = mapped_column(String(64), unique=True, nullable=True)
    client_created_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    receipt_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    override_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    limit_breach: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    status_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class Override(Base):
    """Supervisor-approved exceptions: amount overrides, cash reversals, refunds, adjustments."""

    __tablename__ = "overrides"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))  # AMOUNT | REVERSAL | REFUND | ADJUSTMENT
    payment_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    session_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    vehicle_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    original_paise: Mapped[int] = mapped_column(Integer, default=0)
    new_paise: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[str] = mapped_column(Text)
    requested_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    approved_by: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class CashHandover(Base):
    __tablename__ = "cash_handovers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(24), default="WORKER_TO_SUPERVISOR")
    from_user: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    to_user: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    shift_id: Mapped[Optional[int]] = mapped_column(ForeignKey("shifts.id"), nullable=True)
    expected_paise: Mapped[int] = mapped_column(Integer, default=0)  # system cash-in-hand at declare time
    declared_paise: Mapped[int] = mapped_column(Integer)
    declared_denoms: Mapped[dict] = mapped_column(JSON, default=dict)
    counted_paise: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    counted_denoms: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    variance_paise: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # counted - expected
    status: Mapped[str] = mapped_column(String(12), default="PENDING")  # PENDING|CONFIRMED|REJECTED
    photo_path: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    client_uuid: Mapped[Optional[str]] = mapped_column(String(64), unique=True, nullable=True)
    declared_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)


class BankDeposit(Base):
    __tablename__ = "bank_deposits"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_date: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD local
    amount_paise: Mapped[int] = mapped_column(Integer)
    slip_ref: Mapped[str] = mapped_column(String(64))
    slip_photo_path: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    deposited_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    deposited_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    bank_status: Mapped[str] = mapped_column(String(12), default="PENDING")  # PENDING|CREDITED|MISMATCH
    bank_credited_paise: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    reconciled_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class PaymentDispute(Base):
    __tablename__ = "payment_disputes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), index=True)
    session_id: Mapped[Optional[int]] = mapped_column(ForeignKey("parking_sessions.id"), nullable=True)
    claimed_paise: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    claimed_mode: Mapped[str] = mapped_column(String(8), default="CASH")
    claimed_when: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    zone_id: Mapped[Optional[int]] = mapped_column(ForeignKey("zones.id"), nullable=True)
    worker_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    raised_by_role: Mapped[str] = mapped_column(String(16))  # GUARD|WORKER|SUPERVISOR|CUSTOMER
    raised_by_user: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    alert_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="OPEN")  # OPEN|UPHELD|REJECTED|UNRESOLVED
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resolution_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resolved_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    client_uuid: Mapped[Optional[str]] = mapped_column(String(64), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class Pass(Base):
    __tablename__ = "passes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), index=True)
    vehicle_class: Mapped[str] = mapped_column(String(16))
    pass_type_id: Mapped[int] = mapped_column(ForeignKey("pass_types.id"))
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime)
    ends_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    amount_paise: Mapped[int] = mapped_column(Integer)
    payment_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="PENDING")  # PENDING|ACTIVE|EXPIRED|CANCELLED
    remind_5d_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    remind_1d_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_renew_reminders: Mapped[bool] = mapped_column(Boolean, default=True)
    sold_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    channel: Mapped[str] = mapped_column(String(16), default="WORKER")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Receipt(Base):
    __tablename__ = "receipts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True)
    number: Mapped[str] = mapped_column(String(32), unique=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("payments.id"))
    pass_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    data: Mapped[dict] = mapped_column(JSON)
    phone: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    channel: Mapped[str] = mapped_column(String(12), default="QR")  # SMS|WHATSAPP|QR
    delivery_status: Mapped[str] = mapped_column(String(12), default="PENDING")  # PENDING|SENT|FAILED|SHOWN
    synced_to_relay: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)


class MessageLog(Base):
    __tablename__ = "message_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel: Mapped[str] = mapped_column(String(12))
    to: Mapped[str] = mapped_column(String(16))
    template: Mapped[str] = mapped_column(String(32))
    body: Mapped[str] = mapped_column(Text)
    variables: Mapped[dict] = mapped_column(JSON, default=dict)
    receipt_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(12), index=True)
    provider_ref: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)
    severity: Mapped[str] = mapped_column(String(8), default="WARN")  # INFO|WARN|CRIT
    gate_id: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    session_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    vehicle_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    event_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    acknowledged_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class Device(Base):
    __tablename__ = "devices"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))  # CAMERA | ALERT_UNIT | PHONE | ANPR | RELAY
    gate_id: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    last_seen: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    stalled_alerted: Mapped[bool] = mapped_column(Boolean, default=False)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    table_name: Mapped[str] = mapped_column(String(48), index=True)
    row_id: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(8))  # INSERT | UPDATE | DELETE
    user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    before: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    after: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class RelayInbox(Base):
    """Messages pulled from the cloud relay, applied exactly once (idempotent on msg_id)."""

    __tablename__ = "relay_inbox"
    msg_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON)
    applied_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    result: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)


FINANCIAL_TABLES = {
    "ledger_entries",
    "payments",
    "cash_handovers",
    "bank_deposits",
    "overrides",
    "receipts",
    "passes",
    "payment_disputes",
    "audit_log",
}
