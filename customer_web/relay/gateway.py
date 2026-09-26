"""Relay payment gateway adapters: Razorpay (dynamic UPI QR + signed webhook) and a mock.

The relay has its own adapter (it cannot import the edge code); the Razorpay calls mirror the edge's
`backend/app/adapters/gateway.py`. The txn_ref travels in the QR's notes so webhooks can be matched.
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
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote

import httpx


class GatewayUnavailable(RuntimeError):
    pass


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
    status: str  # PAID | FAILED
    amount_paise: int
    gateway_ref: Optional[str] = None
    utr: Optional[str] = None


def upi_intent_uri(vpa: str, payee: str, amount_paise: int, txn_ref: str, note: str = "Parking") -> str:
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


class RazorpayGateway(PaymentGateway):
    """Razorpay QR Codes API (type=upi_qr, single_use, fixed_amount) + `qr_code.credited` webhook."""

    name = "razorpay"
    base = "https://api.razorpay.com/v1"

    def __init__(self, key_id: str, key_secret: str, webhook_secret: str, timeout: float = 8.0,
                 client: Optional[httpx.Client] = None):
        if not key_id or not key_secret or not webhook_secret:
            raise ValueError("Razorpay needs RELAY_RAZORPAY_KEY_ID, _KEY_SECRET and _WEBHOOK_SECRET")
        self.webhook_secret = webhook_secret
        self.client = client or httpx.Client(base_url=self.base, auth=(key_id, key_secret), timeout=timeout)

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
            "close_by": int(time.time()) + max(expires_in_s, 180), "notes": {"txn_ref": txn_ref},
        })
        return QrResult(order_id=data["id"], upi_uri=data.get("image_content") or "", image_url=data.get("image_url"))

    def fetch_status(self, order_id, txn_ref):
        data = self._req("GET", f"/payments/qr_codes/{order_id}/payments")
        for p in data.get("items", []):
            if p.get("status") == "captured":
                return StatusResult("PAID", p["id"], (p.get("acquirer_data") or {}).get("rrn"), p.get("amount"))
        for p in data.get("items", []):
            if p.get("status") == "failed":
                return StatusResult("FAILED", p["id"])
        return StatusResult("PENDING")

    def parse_webhook(self, body, headers):
        sig = headers.get("x-razorpay-signature", "")
        expected = hmac.new(self.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        if not sig or not hmac.compare_digest(sig, expected):
            raise PermissionError("bad webhook signature")
        payload = json.loads(body)
        ev = payload.get("event")
        pay = ((payload.get("payload") or {}).get("payment") or {}).get("entity") or {}
        qr = ((payload.get("payload") or {}).get("qr_code") or {}).get("entity") or {}
        notes = qr.get("notes") or pay.get("notes") or {}
        if isinstance(notes, list):  # Razorpay sends [] for empty notes
            notes = {}
        if ev in ("qr_code.credited", "payment.captured") and pay:
            return [WebhookEvent(order_id=qr.get("id"), txn_ref=notes.get("txn_ref"), status="PAID",
                                 amount_paise=int(pay.get("amount", 0)), gateway_ref=pay.get("id"),
                                 utr=(pay.get("acquirer_data") or {}).get("rrn"))]
        if ev == "payment.failed" and pay:
            return [WebhookEvent(order_id=None, txn_ref=notes.get("txn_ref"), status="FAILED",
                                 amount_paise=int(pay.get("amount", 0)), gateway_ref=pay.get("id"))]
        return []


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
    """In-memory gateway for demo/tests. `online=False` simulates an outage."""

    name: str = "mock"
    online: bool = True
    vpa: str = "parking@mock"
    webhook_secret: str = "mock-secret"
    orders: dict[str, _MockOrder] = field(default_factory=dict)
    by_ref: dict[str, str] = field(default_factory=dict)
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
        return QrResult(oid, upi_intent_uri(self.vpa, "Station Parking", amount_paise, txn_ref, description[:40]))

    def pay(self, txn_ref: str, amount_paise: Optional[int] = None) -> WebhookEvent:
        """Simulate the customer paying the QR."""
        o = self.orders[self.by_ref[txn_ref]]
        o.status, o.gateway_ref, o.utr = "PAID", "pay_" + uuid.uuid4().hex[:14], str(uuid.uuid4().int)[:12]
        o.paid_at = datetime.now(timezone.utc)
        if amount_paise is not None:
            o.amount_paise = amount_paise
        return WebhookEvent(o.order_id, txn_ref, "PAID", o.amount_paise, o.gateway_ref, o.utr)

    def fetch_status(self, order_id, txn_ref):
        self._check()
        o = self.orders.get(order_id)
        if o is None:
            return StatusResult("PENDING")
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
        return [WebhookEvent(d.get("order_id"), d.get("txn_ref"), d["status"], int(d["amount"]), d.get("gateway_ref"),
                             d.get("utr"))]


def make_gateway(settings) -> PaymentGateway:
    if settings.gateway == "razorpay":
        return RazorpayGateway(settings.razorpay_key_id, settings.razorpay_key_secret, settings.razorpay_webhook_secret)
    return MockGateway()
