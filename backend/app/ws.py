"""WebSocket hub: fans out committed domain events to dashboards, phones and alert units."""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from fastapi import WebSocket

from . import events

log = logging.getLogger(__name__)

# topic -> roles allowed to receive it (devices get only 'exit' for their gate)
TOPIC_ROLES = {
    "anpr.event": {"ADMIN", "SUPERVISOR"},
    "session.opened": {"ADMIN", "SUPERVISOR", "WORKER"},
    "session.closed": {"ADMIN", "SUPERVISOR", "WORKER"},
    "payment.updated": {"ADMIN", "SUPERVISOR", "WORKER"},
    "cash.updated": {"ADMIN", "SUPERVISOR", "WORKER"},
    "handover.pending": {"ADMIN", "SUPERVISOR"},
    "handover.confirmed": {"ADMIN", "SUPERVISOR", "WORKER"},
    "alert": {"ADMIN", "SUPERVISOR", "GUARD"},
    "exit": {"ADMIN", "SUPERVISOR", "GUARD"},
    "review.new": {"ADMIN", "SUPERVISOR"},
    "dispute.new": {"ADMIN", "SUPERVISOR"},
    "pass.activated": {"ADMIN", "SUPERVISOR", "WORKER"},
    "shift.closed": {"ADMIN", "SUPERVISOR"},
    "device.health": {"ADMIN", "SUPERVISOR"},
}


@dataclass(eq=False)
class Client:
    ws: WebSocket
    role: str  # user role, or "DEVICE"
    user_id: Optional[int] = None
    gate_id: Optional[str] = None
    topics: Optional[set[str]] = None
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=500))

    def wants(self, topic: str, data: dict) -> bool:
        if self.role == "DEVICE":
            return topic == "exit" and (self.gate_id is None or data.get("gate_id") == self.gate_id)
        if self.topics and topic not in self.topics:
            return False
        if topic == "payment.updated" and self.role == "WORKER":
            return True
        if topic == "cash.updated" and self.role == "WORKER":
            return data.get("user_id") == self.user_id
        return self.role in TOPIC_ROLES.get(topic, {"ADMIN"})


class Hub:
    def __init__(self) -> None:
        self.clients: set[Client] = set()
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    def attach(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        events.subscribe(self.publish)

    def detach(self) -> None:
        events.unsubscribe(self.publish)

    def publish(self, topic: str, data: dict) -> None:
        """Called from any thread after a commit."""
        if self.loop is None:
            return
        msg = json.dumps({"topic": topic, "data": data, "ts": datetime.now(timezone.utc).isoformat()}, default=str)
        self.loop.call_soon_threadsafe(self._fanout, topic, data, msg)

    def _fanout(self, topic: str, data: dict, msg: str) -> None:
        for c in list(self.clients):
            if c.wants(topic, data):
                try:
                    c.queue.put_nowait(msg)
                except asyncio.QueueFull:
                    log.warning("ws client queue full; dropping message for %s", c.role)

    async def serve(self, client: Client) -> None:
        self.clients.add(client)
        sender = asyncio.create_task(self._sender(client))
        try:
            while True:
                txt = await client.ws.receive_text()
                if txt == "ping":
                    await client.ws.send_text('{"topic":"pong"}')
        except Exception:
            pass
        finally:
            sender.cancel()
            self.clients.discard(client)

    async def _sender(self, client: Client) -> None:
        while True:
            msg = await client.queue.get()
            await client.ws.send_text(msg)


hub = Hub()
