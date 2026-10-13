"""Payment gateway adapters: interface, Razorpay (dynamic UPI QR + webhook), and a mock.

Credentials come from environment variables (PARK_RAZORPAY_*). The mock is used in tests,
the simulator and demo mode; it can simulate an internet outage.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional
from urllib.parse import quote

import httpx

from ..config import get_settings


class GatewayUnavailable(RuntimeError):
    """Gateway unreachable (no internet) — callers fall back to the offline UPI path."""


@dataclass
class QrResult:
    order_id: str
    upi_uri: str
    image_url: Optional[str] = None


@dataclass
class StatusResult:
    status: str  # PAID | PENDING | FAILED
    gateway_ref: Optional[str] = None
    utr: Optional[str] = None
    amount_paise: Optional[int] = None


@dataclass
class WebhookEvent:
    order_id: Optional[str]
    txn_ref: Optional[str]
    status: str  # PAID | FAILED | REFUNDED
    amount_paise: int
    gateway_ref: Optional[str] = None
    utr: Optional[str] = None


@dataclass
class SettlementLine:
    txn_ref: Optional[str]
    amount_paise: int
    gateway_ref: Optional[str] = None
    utr: Optional[str] = None
    settled_at: Optional[datetime] = None
    source: str = "GATEWAY"  # GATEWAY | BANK


@dataclass
class RefundResult:
    refund_id: str
    status: str  # PROCESSED | PENDING | FAILED


def upi_intent_uri(vpa: str, payee: str, amount_paise: int, txn_ref: str, note: str = "Parking") -> str:
    """Standard UPI deep link (NPCI linking spec). Used by the offline path and as a QR payload."""
    return (f"upi://pay?pa={quote(vpa)}&pn={quote(payee)}&am={amount_paise / 100:.2f}&cu=INR"
            f"&tr={quote(txn_ref)}&tn={quote(note)}")


class PaymentGateway(ABC):
    name = "abstract"

    @abstractmethod
    def create_upi_qr(self, amount_paise: int, txn_ref: str, description: str, expires_in_s: int = 900) -> QrResult: ...

    @abstractmethod
    def fetch_status(self, order_id: str, txn_ref: str) -> StatusResult: ...

    @abstractmethod
    def parse_webhook(self, body: bytes, headers: dict[str, str]) -> list[WebhookEvent]: ...

    @abstractmethod
    def refund(self, gateway_ref: str, amount_paise: int, note: str) -> RefundResult: ...

    @abstractmethod
    def fetch_settlements(self, day: date) -> list[SettlementLine]: ...

    def healthy(self) -> bool:
        return True


# ----------------------------------------------------------------------------- Razorpay
class RazorpayGateway(PaymentGateway):
    """Razorpay QR Codes API (type=upi_qr, single_use, fixed amount) + `qr_code.credited` webhook."""

    name = "razorpay"
    base = "https://api.razorpay.com/v1"

    def __init__(self, key_id: str, key_secret: str, webhook_secret: str, timeout: float = 8.0):
        if not key_id or not key_secret:
            raise ValueError("Razorpay credentials missing (PARK_RAZORPAY_KEY_ID / PARK_RAZORPAY_KEY_SECRET)")
        self.webhook_secret = webhook_secret
        self.client = httpx.Client(base_url=self.base, auth=(key_id, key_secret), timeout=timeout)

    def _req(self, method: str, url: str, **kw) -> dict:
        try:
            r = self.client.request(method, url, **kw)
        except httpx.TransportError as e:
            raise GatewayUnavailable(str(e)) from e
        if r.status_code >= 500:
            raise GatewayUnavailable(f"razorpay {r.status_code}")
        r.raise_for_status()
        return r.json()

    def create_upi_qr(self, amount_paise, txn_ref, description, expires_in_s=900):
        data = self._req("POST", "/payments/qr_codes", json={
            "type": "upi_qr", "name": "Parking", "usage": "single_use", "fixed_amount": True,
            "payment_amount": amount_paise, "description": description[:100],
            "close_by": int(time.time()) + max(expires_in_s, 180),
            "notes": {"txn_ref": txn_ref},
        })
        return QrResult(order_id=data["id"], upi_uri=data.get("image_content") or "", image_url=data.get("image_url"))

    def fetch_status(self, order_id, txn_ref):
        data = self._req("GET", f"/payments/qr_codes/{order_id}/payments")
        for p in data.get("items", []):
            if p.get("status") == "captured":
                return StatusResult("PAID", p["id"], (p.get("acquirer_data") or {}).get("rrn"), p.get("amount"))
            if p.get("status") == "failed":
                return StatusResult("FAILED", p["id"])
        return StatusResult("PENDING")

    def parse_webhook(self, body, headers):
        sig = headers.get("x-razorpay-signature", "")
        expected = hmac.new(self.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            raise PermissionError("bad webhook signature")
        payload = json.loads(body)
        ev = payload.get("event")
        out = []
        pay = ((payload.get("payload") or {}).get("payment") or {}).get("entity") or {}
        qr = ((payload.get("payload") or {}).get("qr_code") or {}).get("entity") or {}
        if ev in ("qr_code.credited", "payment.captured") and pay:
            out.append(WebhookEvent(order_id=qr.get("id"), txn_ref=((qr.get("notes") or pay.get("notes") or {}).get("txn_ref")),
                                    status="PAID", amount_paise=int(pay.get("amount", 0)), gateway_ref=pay.get("id"),
                                    utr=(pay.get("acquirer_data") or {}).get("rrn")))
        elif ev == "payment.failed" and pay:
            out.append(WebhookEvent(order_id=None, txn_ref=(pay.get("notes") or {}).get("txn_ref"), status="FAILED",
                                    amount_paise=int(pay.get("amount", 0)), gateway_ref=pay.get("id")))
        return out

    def refund(self, gateway_ref, amount_paise, note):
        data = self._req("POST", f"/payments/{gateway_ref}/refund", json={"amount": amount_paise, "notes": {"reason": note[:200]}})
        return RefundResult(data["id"], "PROCESSED" if data.get("status") == "processed" else "PENDING")

    def fetch_settlements(self, day):
        data = self._req("GET", "/settlements/recon/combined", params={"year": day.year, "month": day.month, "day": day.day, "count": 1000})
        out = []
        for it in data.get("items", []):
            if it.get("type") != "payment":
                continue
            notes = it.get("notes") or {}
            if isinstance(notes, str):
                try:
                    notes = json.loads(notes)
                except ValueError:
                    notes = {}
            out.append(SettlementLine(txn_ref=notes.get("txn_ref"), amount_paise=int(it.get("amount", 0)),
                                      gateway_ref=it.get("entity_id"), utr=it.get("settlement_utr"),
                                      settled_at=datetime.fromtimestamp(it["settled_at"], tz=timezone.utc) if it.get("settled_at") else None))
        return out

    def healthy(self):
        try:
            self.client.get("/payments", params={"count": 1}, timeout=3.0)
            return True
        except httpx.HTTPError:
            return False


# ----------------------------------------------------------------------------- Mock
@dataclass
class _MockOrder:
    order_id: str
    txn_ref: str
    amount_paise: int
    status: str = "PENDING"
    gateway_ref: Optional[str] = None
    utr: Optional[str] = None
    paid_at: Optional[datetime] = None


@dataclass
class MockGateway(PaymentGateway):
    """In-memory gateway. `online=False` simulates an outage; `auto_pay` pays QRs instantly."""

    name: str = "mock"
    online: bool = True
    auto_pay: bool = False
    orders: dict[str, _MockOrder] = field(default_factory=dict)
    by_ref: dict[str, str] = field(default_factory=dict)
    offline_credits: list[SettlementLine] = field(default_factory=list)
    refunds: list[tuple[str, int]] = field(default_factory=list)
    webhook_secret: str = "mock-secret"
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _check(self):
        if not self.online:
            raise GatewayUnavailable("mock gateway offline")

    def create_upi_qr(self, amount_paise, txn_ref, description, expires_in_s=900):
        self._check()
        with self._lock:
            oid = "qr_" + uuid.uuid4().hex[:14]
            self.orders[oid] = _MockOrder(oid, txn_ref, amount_paise)
            self.by_ref[txn_ref] = oid
        if self.auto_pay:
            self.pay(txn_ref)
        return QrResult(oid, upi_intent_uri("mock@upi", "Mock Parking", amount_paise, txn_ref))

    def pay(self, txn_ref: str, at: Optional[datetime] = None) -> WebhookEvent:
        """Simulate the customer paying a dynamic QR."""
        o = self.orders[self.by_ref[txn_ref]]
        o.status, o.gateway_ref, o.utr = "PAID", "pay_" + uuid.uuid4().hex[:14], str(uuid.uuid4().int)[:12]
        o.paid_at = at or datetime.now(timezone.utc)
        return WebhookEvent(o.order_id, txn_ref, "PAID", o.amount_paise, o.gateway_ref, o.utr)

    def credit_offline(self, txn_ref: str, amount_paise: int, at: Optional[datetime] = None) -> None:
        """Simulate a customer paying an offline intent QR: it shows up in the bank/settlement report."""
        self.offline_credits.append(SettlementLine(txn_ref, amount_paise, "pay_" + uuid.uuid4().hex[:14],
                                                   str(uuid.uuid4().int)[:12], at or datetime.now(timezone.utc), "BANK"))

    def fetch_status(self, order_id, txn_ref):
        self._check()
        o = self.orders.get(order_id)
        if o is None:
            return StatusResult("FAILED")
        return StatusResult(o.status, o.gateway_ref, o.utr, o.amount_paise)

    def webhook_body(self, ev: WebhookEvent) -> tuple[bytes, dict[str, str]]:
        body = json.dumps({"event": "mock.paid", "order_id": ev.order_id, "txn_ref": ev.txn_ref, "status": ev.status,
                           "amount": ev.amount_paise, "gateway_ref": ev.gateway_ref, "utr": ev.utr}).encode()
        sig = hmac.new(self.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        return body, {"x-mock-signature": sig}

    def parse_webhook(self, body, headers):
        expected = hmac.new(self.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(headers.get("x-mock-signature", ""), expected):
            raise PermissionError("bad webhook signature")
        d = json.loads(body)
        return [WebhookEvent(d.get("order_id"), d.get("txn_ref"), d["status"], int(d["amount"]), d.get("gateway_ref"), d.get("utr"))]

    def refund(self, gateway_ref, amount_paise, note):
        self._check()
        self.refunds.append((gateway_ref, amount_paise))
        return RefundResult("rfnd_" + uuid.uuid4().hex[:10], "PROCESSED")

    def fetch_settlements(self, day):
        self._check()
        out = [SettlementLine(o.txn_ref, o.amount_paise, o.gateway_ref, o.utr, o.paid_at)
               for o in self.orders.values() if o.status == "PAID" and o.paid_at]
        out += self.offline_credits
        return out

    def healthy(self):
        return self.online


_gateway: Optional[PaymentGateway] = None


def get_gateway() -> PaymentGateway:
    global _gateway
    if _gateway is None:
        s = get_settings()
        if s.gateway == "razorpay":
            _gateway = RazorpayGateway(s.razorpay_key_id, s.razorpay_key_secret, s.razorpay_webhook_secret)
        else:
            _gateway = MockGateway(auto_pay=False)
    return _gateway


def set_gateway(gw: PaymentGateway) -> None:
    global _gateway
    _gateway = gw
