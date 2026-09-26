"""Audit trail and append-only enforcement, wired as SQLAlchemy session events.

* Every INSERT/UPDATE/DELETE on audited tables writes an audit_log row with before/after.
* Hard deletes of financial rows raise.
* Ledger entries are immutable (no updates either).
"""
from __future__ import annotations

import contextvars
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import event, insert, inspect
from sqlalchemy.orm import Session

from .db import SessionLocal, utcnow
from .models import FINANCIAL_TABLES, AuditLog

current_user_id: contextvars.ContextVar[int | None] = contextvars.ContextVar("current_user_id", default=None)

SKIP_TABLES = {"audit_log", "devices", "message_log"}
IMMUTABLE_TABLES = {"ledger_entries", "audit_log"}


class AppendOnlyViolation(RuntimeError):
    pass


def _jsonable(v: Any) -> Any:
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (list, dict, str, int, float, bool)) or v is None:
        return v
    return str(v)


def _row_dict(obj) -> dict:
    mapper = inspect(obj).mapper
    return {c.key: _jsonable(getattr(obj, c.key)) for c in mapper.column_attrs}


def _pk(obj) -> str:
    ident = inspect(obj).identity
    if ident:
        return ",".join(str(i) for i in ident)
    return ""


@event.listens_for(SessionLocal, "before_flush")
def _guard(session: Session, flush_context, instances) -> None:
    for obj in session.deleted:
        table = obj.__table__.name
        if table in FINANCIAL_TABLES:
            raise AppendOnlyViolation(f"hard delete of financial record {table} is not allowed")
    for obj in session.dirty:
        table = obj.__table__.name
        if table in IMMUTABLE_TABLES and session.is_modified(obj, include_collections=False):
            raise AppendOnlyViolation(f"{table} rows are immutable; post a new entry instead")
    # capture 'before' images of updates for after_flush
    befores = session.info.setdefault("_audit_before", {})
    for obj in session.dirty:
        table = obj.__table__.name
        if table in SKIP_TABLES or not session.is_modified(obj, include_collections=False):
            continue
        state = inspect(obj)
        before = {}
        for attr in state.mapper.column_attrs:
            hist = state.attrs[attr.key].history
            if hist.has_changes():
                before[attr.key] = _jsonable(hist.deleted[0] if hist.deleted else None)
        befores[id(obj)] = before


@event.listens_for(SessionLocal, "after_flush")
def _record(session: Session, flush_context) -> None:
    uid = session.info.get("user_id") or current_user_id.get()
    now = utcnow()
    rows: list[dict] = []
    befores = session.info.pop("_audit_before", {})
    for obj in session.new:
        table = obj.__table__.name
        if table in SKIP_TABLES:
            continue
        rows.append(dict(table_name=table, row_id=_pk(obj), action="INSERT", user_id=uid,
                         before=None, after=_row_dict(obj), created_at=now))
    for obj in session.dirty:
        table = obj.__table__.name
        if table in SKIP_TABLES or id(obj) not in befores:
            continue
        before = befores[id(obj)]
        after = {k: _jsonable(getattr(obj, k)) for k in before}
        rows.append(dict(table_name=table, row_id=_pk(obj), action="UPDATE", user_id=uid,
                         before=before, after=after, created_at=now))
    for obj in session.deleted:
        table = obj.__table__.name
        if table in SKIP_TABLES:
            continue
        rows.append(dict(table_name=table, row_id=_pk(obj), action="DELETE", user_id=uid,
                         before=_row_dict(obj), after=None, created_at=now))
    if rows:
        session.connection().execute(insert(AuditLog.__table__), rows)
