"""Relay side of the edge <-> relay contract in backend/app/relay_sync.py.

  POST /sync/push              edge -> relay: open entries, vehicles, receipts, pass types, settings
  GET  /sync/pull?after=<cur>  relay -> edge: queued customer actions (outbox)
  POST /sync/ack {"ids":[..]}  edge confirms messages applied

Delivery semantics: at-least-once. The edge applies each message exactly once (relay_inbox keyed on
the message id) and dedupes payments by txn_ref, so redelivery is always safe. `pull` returns every
message not yet acked, oldest first (the cursor is informational: it is the highest seq handed out),
so a message that failed to apply on the edge is offered again on the next pull instead of being
skipped when the edge advances its cursor.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from .db import utcnow
from .deps import get_db, settings_of
from .models import KV, Entry, Outbox, PassType, Receipt, Vehicle
from .plates import mask, normalise
from .security import check_relay_key

log = logging.getLogger(__name__)

router = APIRouter(prefix="/sync", tags=["sync"])

KNOWN_KINDS = {"SELF_PAY_PAID", "DUES_PAID", "PASS_PAID", "DISPUTE", "CONTACT_VERIFIED", "DATA_REQUEST"}
PULL_LIMIT = 200


# ------------------------------------------------------------------ kv helpers
def kv_get(db: Session, key: str, default: Any = None) -> Any:
    row = db.get(KV, key)
    return row.value if row is not None and row.value is not None else default


def kv_set(db: Session, key: str, value: Any) -> None:
    row = db.get(KV, key)
    if row is None:
        db.add(KV(key=key, value=value))
    else:
        row.value = value


def lot_settings(db: Session) -> dict[str, Any]:
    s = kv_get(db, "settings", {}) or {}
    return {"lot_name": s.get("lot_name") or "Station Parking",
            "receipt_footer": s.get("receipt_footer") or
            "Final charge is calculated on actual time; any difference is adjusted on your next visit.",
            "duration_buttons": s.get("duration_buttons") or [], "upi_vpa": s.get("upi_vpa"),
            "upi_payee_name": s.get("upi_payee_name"), "gstin": s.get("gstin") or None}


def _dt(v: Optional[str]) -> Optional[datetime]:
    if not v:
        return None
    d = datetime.fromisoformat(v)
    if d.tzinfo is None:
        from datetime import timezone

        d = d.replace(tzinfo=timezone.utc)
    return d


# ------------------------------------------------------------------ push
def apply_push(db: Session, body: dict[str, Any]) -> dict[str, int]:
    now = utcnow()
    full = bool(body.get("full"))
    seen: set[int] = set()
    n_entries = 0
    for e in body.get("entries") or []:
        sid = int(e["session_id"])
        seen.add(sid)
        plate = normalise(e["plate"])
        row = db.get(Entry, sid)
        if row is None:
            row = Entry(session_id=sid)
            db.add(row)
        row.plate = plate
        row.masked_plate = e.get("masked_plate") or mask(plate)
        row.vehicle_class = e.get("vehicle_class") or "BIKE"
        row.entry_at = _dt(e["entry_at"])
        row.status = e.get("status") or "OPEN"
        row.gate_id = e.get("gate_id")
        row.quotes = {str(k): int(v) for k, v in (e.get("quotes") or {}).items()}
        if e.get("thumb_b64"):  # delta pushes send null: keep the thumbnail we already have
            row.thumb_b64 = e["thumb_b64"]
        row.updated_at = now
        n_entries += 1
    removed = 0
    if full:
        # a full push lists every open session: anything else has exited / been closed
        stmt = delete(Entry)
        if seen:
            stmt = stmt.where(Entry.session_id.not_in(seen))
        removed = db.execute(stmt).rowcount or 0

    n_veh = 0
    for v in body.get("vehicles") or []:
        plate = normalise(v["plate"])
        row = db.get(Vehicle, plate)
        if row is None:
            row = Vehicle(plate=plate)
            db.add(row)
        row.vehicle_class = v.get("vehicle_class") or "BIKE"
        row.balance_paise = int(v.get("balance_paise") or 0)
        row.phone_hash = v.get("phone_hash")
        p = v.get("pass")
        if isinstance(p, dict):
            p = {k: p.get(k) for k in ("pass_type", "pass_type_id", "vehicle_class", "starts_on", "ends_on", "status")}
        row.pass_ = p  # the edge includes the owner's phone in pass dicts: never stored here
        row.history = v.get("history") or []
        row.updated_at = now
        n_veh += 1

    n_rec = 0
    for r in body.get("receipts") or []:
        row = db.get(Receipt, r["code"])
        data = r.get("data") or {}
        if row is None:
            row = Receipt(code=r["code"])
            db.add(row)
        row.number = r.get("number") or data.get("number") or ""
        row.data = data
        row.txn_ref = data.get("txn_ref")
        row.created_at = _dt(r.get("created_at")) or now
        row.received_at = now
        n_rec += 1

    if "pass_types" in body and body["pass_types"] is not None:
        ids = set()
        for pt in body["pass_types"]:
            ids.add(int(pt["id"]))
            row = db.get(PassType, int(pt["id"]))
            if row is None:
                row = PassType(id=int(pt["id"]))
                db.add(row)
            row.vehicle_class, row.name = pt["vehicle_class"], pt["name"]
            row.period_unit, row.period_value = pt.get("period_unit") or "MONTH", int(pt.get("period_value") or 1)
            row.price_paise, row.active = int(pt["price_paise"]), True
        db.flush()
        stmt = update(PassType).values(active=False)
        if ids:
            stmt = stmt.where(PassType.id.not_in(ids))
        db.execute(stmt)

    if isinstance(body.get("settings"), dict):
        merged = dict(kv_get(db, "settings", {}) or {})
        merged.update(body["settings"])
        kv_set(db, "settings", merged)
    kv_set(db, "last_push_at", now.isoformat())
    if full:
        kv_set(db, "last_full_push_at", now.isoformat())
    kv_set(db, "edge_generated_at", body.get("generated_at"))
    db.flush()
    return {"entries": n_entries, "removed": removed, "vehicles": n_veh, "receipts": n_rec}


# ------------------------------------------------------------------ outbox
def enqueue(db: Session, kind: str, payload: dict[str, Any]) -> Outbox:
    if kind not in KNOWN_KINDS:
        raise ValueError(f"unknown message kind {kind}")
    msg = Outbox(id=uuid.uuid4().hex, kind=kind, payload=payload, created_at=utcnow())
    db.add(msg)
    db.flush()
    return msg


def message_dict(m: Outbox) -> dict[str, Any]:
    return {"id": m.id, "kind": m.kind, "payload": m.payload, "created_at": m.created_at.isoformat()}


def pull(db: Session, after: Optional[str], limit: int = PULL_LIMIT) -> dict[str, Any]:
    rows = db.scalars(select(Outbox).where(Outbox.acked_at.is_(None)).order_by(Outbox.seq).limit(limit)).all()
    now = utcnow()
    for m in rows:
        m.pull_count = (m.pull_count or 0) + 1
        m.first_pulled_at = m.first_pulled_at or now
    try:
        prev = int(after) if after else 0
    except ValueError:
        prev = 0
    cursor = max([prev] + [m.seq for m in rows])
    return {"messages": [message_dict(m) for m in rows], "cursor": str(cursor)}


def ack(db: Session, ids: list[str]) -> int:
    if not ids:
        return 0
    res = db.execute(update(Outbox).where(Outbox.id.in_([str(i) for i in ids]), Outbox.acked_at.is_(None))
                     .values(acked_at=utcnow()))
    return res.rowcount or 0


def deliver_direct(edge_url: str, relay_key: str, messages: list[dict[str, Any]],
                   client: Optional[httpx.Client] = None) -> Optional[list[dict]]:
    """Optional fast path: POST {EDGE_URL}/api/relay/apply. Failures are fine: the edge pulls later."""
    if not edge_url or not messages:
        return None
    c = client or httpx.Client(timeout=5.0)
    try:
        r = c.post(edge_url.rstrip("/") + "/api/relay/apply", json={"messages": messages},
                   headers={"X-Relay-Key": relay_key})
        r.raise_for_status()
        return r.json().get("results")
    except (httpx.HTTPError, ValueError) as exc:
        log.info("direct delivery to edge failed (%s); will be pulled", type(exc).__name__)
        return None
    finally:
        if client is None:
            c.close()


def deliver_direct_and_record(app_state, msg_ids: list[str]) -> None:
    """Background task: try the direct path; the message stays queued until the edge acks it."""
    s = app_state.settings
    if not s.edge_url:
        return
    db: Session = app_state.sessionmaker()
    try:
        msgs = db.scalars(select(Outbox).where(Outbox.id.in_(msg_ids))).all()
        results = deliver_direct(s.edge_url, s.relay_api_key, [message_dict(m) for m in msgs])
        for m in msgs:
            m.direct_status = "APPLIED" if results is not None else "FAILED"
        db.commit()
    finally:
        db.close()


# ------------------------------------------------------------------ routes
def relay_auth(request: Request, x_relay_key: Optional[str] = Header(None)) -> None:
    check_relay_key(settings_of(request), x_relay_key)


@router.post("/push", dependencies=[Depends(relay_auth)])
def push(body: dict[str, Any], request: Request, db: Session = Depends(get_db)):
    counts = apply_push(db, body)
    db.commit()
    last = kv_get(db, "last_housekeeping_at")
    if not last or utcnow() - datetime.fromisoformat(last) > timedelta(hours=1):
        run_housekeeping(db, settings_of(request).phone_retention_days)
        kv_set(db, "last_housekeeping_at", utcnow().isoformat())
        db.commit()
    return {"ok": True, **counts}


def run_housekeeping(db: Session, phone_retention_days: int) -> None:
    """Expired OTPs, old rate-limit rows, phone numbers on old payment intents, old acked messages."""
    from . import otp as otp_svc
    from .payments import purge_phones
    from .security import purge_rate_events

    otp_svc.purge(db)
    purge_rate_events(db)
    purge_phones(db, phone_retention_days)
    db.execute(delete(Outbox).where(Outbox.acked_at.is_not(None), Outbox.kind != "DATA_REQUEST",
                                    Outbox.acked_at < utcnow() - timedelta(days=phone_retention_days)))


@router.get("/pull", dependencies=[Depends(relay_auth)])
def pull_route(after: str = "", db: Session = Depends(get_db)):
    out = pull(db, after)
    db.commit()
    return out


@router.post("/ack", dependencies=[Depends(relay_auth)])
def ack_route(body: dict[str, Any], db: Session = Depends(get_db)):
    n = ack(db, list(body.get("ids") or []))
    db.commit()
    return {"ok": True, "acked": n}


@router.get("/status", dependencies=[Depends(relay_auth)])
def status_route(db: Session = Depends(get_db)):
    pending = db.scalars(select(Outbox).where(Outbox.acked_at.is_(None)).order_by(Outbox.seq)).all()
    oldest = pending[0].created_at if pending else None
    return {"last_push_at": kv_get(db, "last_push_at"), "last_full_push_at": kv_get(db, "last_full_push_at"),
            "pending_messages": len(pending), "oldest_pending_at": oldest.isoformat() if oldest else None,
            "stuck_messages": [m.id for m in pending if (m.pull_count or 0) >= 5]}


@router.get("/data-requests", dependencies=[Depends(relay_auth)])
def data_requests(days: int = 90, db: Session = Depends(get_db)):
    """DPDP requests raised on the privacy page (the edge ignores DATA_REQUEST; the operator reads them here)."""
    since = utcnow() - timedelta(days=days)
    rows = db.scalars(select(Outbox).where(Outbox.kind == "DATA_REQUEST", Outbox.created_at >= since)
                      .order_by(Outbox.seq.desc())).all()
    return [message_dict(m) | {"acked_at": m.acked_at.isoformat() if m.acked_at else None} for m in rows]
