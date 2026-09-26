"""Relay tables. The edge server is the source of truth: entries/vehicles/receipts/pass types are
replicas it pushes; outbox messages and payment intents are the relay's own records."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, Boolean, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, UTCDateTime, utcnow


# ------------------------------------------------------------------ replicas pushed by the edge
class Entry(Base):
    """An open parking session (OPEN / PREPAID) as last pushed by the edge."""

    __tablename__ = "entries"
    session_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    plate: Mapped[str] = mapped_column(String(16), index=True)
    masked_plate: Mapped[str] = mapped_column(String(32))
    vehicle_class: Mapped[str] = mapped_column(String(16))
    entry_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    status: Mapped[str] = mapped_column(String(16))
    gate_id: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    quotes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    thumb_b64: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # previous dues / credit already included in `quotes` (pushed by the edge; None from older edges)
    dues_paise: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    credit_paise: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    # set by the relay when a self-pay for this session is confirmed (not overwritten by pushes)
    relay_paid_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)


class Vehicle(Base):
    __tablename__ = "vehicles"
    plate: Mapped[str] = mapped_column(String(16), primary_key=True)
    vehicle_class: Mapped[str] = mapped_column(String(16))
    balance_paise: Mapped[int] = mapped_column(Integer, default=0)
    phone_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    pass_: Mapped[Optional[dict[str, Any]]] = mapped_column("pass", JSON, nullable=True)
    history: Mapped[list[Any]] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Receipt(Base):
    __tablename__ = "receipts"
    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    number: Mapped[str] = mapped_column(String(32))
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    txn_ref: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class PassType(Base):
    __tablename__ = "pass_types"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    vehicle_class: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(64))
    period_unit: Mapped[str] = mapped_column(String(16))
    period_value: Mapped[int] = mapped_column(Integer)
    price_paise: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class KV(Base):
    """Pushed lot settings (lot_name, receipt_footer, ...) and relay state (last_push_at, ...)."""

    __tablename__ = "kv"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=True)


# ------------------------------------------------------------------ relay-owned
class Outbox(Base):
    """Customer actions queued for the edge (GET /sync/pull, POST /sync/ack)."""

    __tablename__ = "outbox"
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    first_pulled_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    pull_count: Mapped[int] = mapped_column(Integer, default=0)
    acked_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    direct_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)


class PaymentIntent(Base):
    """A UPI payment started on the relay. Queued to the edge only once the gateway confirms it."""

    __tablename__ = "payment_intents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(16))  # SELF_PAY | DUES | PASS
    plate: Mapped[str] = mapped_column(String(16))
    plate_revealed: Mapped[bool] = mapped_column(Boolean, default=False)  # customer typed the full plate
    session_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    vehicle_class: Mapped[str] = mapped_column(String(16))
    duration_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    amount_paise: Mapped[int] = mapped_column(Integer)
    base_paise: Mapped[int] = mapped_column(Integer, default=0)
    dues_paise: Mapped[int] = mapped_column(Integer, default=0)
    pass_type_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)  # OTP-verified only
    queue_contact_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    txn_ref: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    gateway: Mapped[str] = mapped_column(String(16))
    order_id: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
    upi_uri: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    qr_image_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="PENDING")  # PENDING | PAID | FAILED | EXPIRED
    gateway_ref: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    utr: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    paid_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    last_polled_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)


class OtpCode(Base):
    __tablename__ = "otp_codes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    phone_key: Mapped[str] = mapped_column(String(64), index=True)  # HMAC(phone), never the number
    code_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    used_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)


class RateEvent(Base):
    __tablename__ = "rate_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


Index("ix_rate_key_time", RateEvent.key, RateEvent.created_at)
