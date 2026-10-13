"""In-process event bus. Domain code calls `emit(db, topic, payload)`; events are published
to subscribers (the WebSocket hub) only after the DB transaction commits."""
from __future__ import annotations

from typing import Any, Callable

from sqlalchemy import event as sa_event
from sqlalchemy.orm import Session

from .db import SessionLocal

Subscriber = Callable[[str, dict], None]
_subscribers: list[Subscriber] = []


def subscribe(fn: Subscriber) -> None:
    _subscribers.append(fn)


def unsubscribe(fn: Subscriber) -> None:
    if fn in _subscribers:
        _subscribers.remove(fn)


def emit(db: Session, topic: str, payload: dict[str, Any]) -> None:
    db.info.setdefault("_pending_events", []).append((topic, payload))


@sa_event.listens_for(SessionLocal, "after_commit")
def _publish(session: Session) -> None:
    pending = session.info.pop("_pending_events", [])
    for topic, payload in pending:
        for fn in list(_subscribers):
            try:
                fn(topic, payload)
            except Exception:  # pragma: no cover - subscribers must not break commits
                import logging

                logging.getLogger(__name__).exception("event subscriber failed")


@sa_event.listens_for(SessionLocal, "after_rollback")
def _discard(session: Session) -> None:
    session.info.pop("_pending_events", None)
