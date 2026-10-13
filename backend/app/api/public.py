"""Unauthenticated / machine endpoints: gateway webhook, public receipt JSON, relay hooks, demo helpers."""
from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..adapters.gateway import MockGateway, get_gateway
from ..config import get_settings
from ..db import utcnow
from ..domain import payments
from ..domain import sessions as sess_svc
from ..models import Payment, Receipt
from .. import relay_sync
from .deps import get_db, relay_key

router = APIRouter()


@router.post("/api/payments/webhook")
async def webhook(request: Request, db: Session = Depends(get_db)):
    body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    try:
        updated = payments.handle_webhook(db, body, headers)
    except PermissionError:
        raise HTTPException(401, "bad signature")
    db.commit()
    return {"ok": True, "updated": [p.id for p in updated]}


@router.get("/api/public/receipts/{code}")
def receipt_json(code: str, db: Session = Depends(get_db)):
    r = db.scalars(select(Receipt).where(Receipt.code == code)).first()
    if r is None:
        raise HTTPException(404, "receipt not found")
    return {"code": r.code, "number": r.number, **r.data}


@router.get("/r/{code}", response_class=HTMLResponse)
def receipt_page(code: str, db: Session = Depends(get_db)):
    """Local copy of the receipt page (the public one is served by the cloud relay)."""
    r = db.scalars(select(Receipt).where(Receipt.code == code)).first()
    if r is None:
        raise HTTPException(404, "receipt not found")
    d = r.data
    rs = lambda p: f"₹{(p or 0) / 100:.2f}"  # noqa: E731
    rows = [("Receipt", r.number), ("Vehicle", f"{d['plate']} ({d['vehicle_class']})"), ("Paid", rs(d["amount_paise"])),
            ("Mode", d["mode"]), ("Paid at", d["paid_at"][:16].replace("T", " "))]
    if d.get("entry_time"):
        rows.append(("Entry", d["entry_time"][:16].replace("T", " ")))
    if d.get("duration_paid_minutes"):
        rows.append(("Duration paid", f"{d['duration_paid_minutes'] // 60} h"))
    if d.get("dues_cleared_paise"):
        rows.append(("Previous dues cleared", rs(d["dues_cleared_paise"])))
    if d.get("upi_ref"):
        rows.append(("UPI ref", d["upi_ref"]))
    if d.get("pass"):
        rows.append(("Pass", f"{d['pass']['type']} {d['pass']['valid_from']} to {d['pass']['valid_till']}"))
    if d.get("gstin"):
        rows.append(("GSTIN", d["gstin"]))
    trs = "".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in rows)
    return f"""<!doctype html><html><head><meta name=viewport content="width=device-width,initial-scale=1">
<title>Receipt {r.number}</title><style>body{{font-family:system-ui;max-width:420px;margin:16px auto;padding:0 16px}}
th{{text-align:left;color:#555;font-weight:500;padding:6px 8px 6px 0}}td{{padding:6px 0}}.f{{color:#444;font-size:14px;
border-top:1px solid #ddd;padding-top:12px}}</style></head><body><h2>{d['lot_name']}</h2><table>{trs}</table>
<p class=f>{d['footer']}</p></body></html>"""


class RelayMessages(BaseModel):
    messages: list[dict[str, Any]]


@router.post("/api/relay/apply")
def relay_apply(body: RelayMessages, db: Session = Depends(get_db), _: str = Depends(relay_key)):
    """Direct push path (when the relay can reach the edge, e.g. over a VPN); same semantics as pull."""
    out = []
    for m in body.messages:
        out.append(relay_sync.apply_message(db, m))
        db.commit()
    return {"results": out}


@router.get("/api/relay/snapshot")
def relay_snapshot(full: bool = True, db: Session = Depends(get_db), _: str = Depends(relay_key)):
    body = relay_sync.build_push(db, None)
    body.pop("receipt_ids", None)
    return body


# ------------------------------------------------------------------ demo helpers (PARK_DEMO_MODE=true only)
def _demo():
    if not get_settings().demo_mode:
        raise HTTPException(404, "demo mode is off")


class DemoGateway(BaseModel):
    online: bool


@router.post("/api/demo/gateway")
def demo_gateway(body: DemoGateway):
    _demo()
    gw = get_gateway()
    if isinstance(gw, MockGateway):
        gw.online = body.online
    return {"online": gw.healthy()}


@router.post("/api/demo/pay/{payment_id}")
def demo_pay(payment_id: int, db: Session = Depends(get_db)):
    """Simulate the customer completing a UPI payment (mock gateway sends the webhook)."""
    _demo()
    p = db.get(Payment, payment_id)
    gw = get_gateway()
    if p is None or not isinstance(gw, MockGateway):
        raise HTTPException(404, "payment not found / not mock gateway")
    if p.offline:
        gw.credit_offline(p.txn_ref, p.amount_paise)
        return {"credited_offline": True}
    body, headers = gw.webhook_body(gw.pay(p.txn_ref))
    payments.handle_webhook(db, body, headers)
    db.commit()
    return payments.payment_dict(db, p)


class DemoEvent(BaseModel):
    gate_id: str
    direction: str
    plate: Optional[str] = None
    vehicle_class: str = "BIKE"
    confidence: float = 0.95


@router.post("/api/demo/event")
def demo_event(body: DemoEvent, db: Session = Depends(get_db)):
    _demo()
    payload = {"event_id": str(uuid.uuid4()), "gate_id": body.gate_id, "camera_ids": [f"{body.gate_id}-L"],
               "direction": body.direction, "vehicle_class": body.vehicle_class,
               "ts_ms": int(utcnow().timestamp() * 1000), "status": "READ" if body.plate else "UNREAD",
               "plate": body.plate, "confidence": body.confidence if body.plate else 0.0,
               "candidates": [{"plate": body.plate, "confidence": body.confidence}] if body.plate else [], "images": {}}
    res = sess_svc.ingest_event(db, payload)
    db.commit()
    return {"event_id": res.event.id, "status": res.event.status, "session_id": res.event.session_id,
            "exit": res.exit_display}
