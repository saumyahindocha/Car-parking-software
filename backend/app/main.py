"""FastAPI application for the edge server."""
from __future__ import annotations

import asyncio
import logging
import mimetypes
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import domain  # noqa: F401  registers audit + event hooks
from .adapters.gateway import GatewayUnavailable
from .api import core, manage, public, reports_api, worker
from .audit import AppendOnlyViolation
from .config import get_settings
from .db import SessionLocal, get_engine
from .domain.cash import CashError
from .domain.disputes import DisputeError
from .domain.payments import CashLimitReached, PaymentError
from .models import Role, User
from .security import read_token
from .ws import Client, hub

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("parking")


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_engine()
    hub.attach(asyncio.get_running_loop())
    sched = None
    if get_settings().run_background_jobs:
        from .jobs import Scheduler

        sched = Scheduler()
        sched.start()
    yield
    hub.detach()
    if sched:
        sched.stop()


app = FastAPI(title="Station Parking — Edge API", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

for r in (core.router, worker.router, manage.router, reports_api.router, public.router):
    app.include_router(r)


def _err(status: int, msg: str) -> JSONResponse:
    return JSONResponse({"detail": msg}, status_code=status)


@app.exception_handler(CashLimitReached)
async def _cash_limit(_: Request, e: CashLimitReached):
    return _err(423, str(e))


@app.exception_handler(PaymentError)
@app.exception_handler(CashError)
@app.exception_handler(DisputeError)
@app.exception_handler(ValueError)
async def _bad(_: Request, e: Exception):
    return _err(400, str(e))


@app.exception_handler(LookupError)
async def _nf(_: Request, e: LookupError):
    return _err(404, str(e))


@app.exception_handler(PermissionError)
async def _perm(_: Request, e: PermissionError):
    return _err(403, str(e))


@app.exception_handler(AppendOnlyViolation)
async def _append(_: Request, e: AppendOnlyViolation):
    return _err(409, str(e))


@app.exception_handler(GatewayUnavailable)
async def _gw(_: Request, e: GatewayUnavailable):
    return _err(503, f"payment gateway unreachable: {e}")


# ------------------------------------------------------------------ websockets
@app.websocket("/ws")
async def ws_users(ws: WebSocket, token: str = "", topics: str = ""):
    claims = read_token(token)
    if not claims:
        await ws.close(code=4401)
        return
    db = SessionLocal()
    try:
        u = db.get(User, claims["uid"])
        role = u.role if u and u.active else None
    finally:
        db.close()
    if role not in Role.ALL:
        await ws.close(code=4403)
        return
    await ws.accept()
    await hub.serve(Client(ws=ws, role=role, user_id=claims["uid"],
                           topics=set(t for t in topics.split(",") if t) or None))


@app.websocket("/ws/device")
async def ws_device(ws: WebSocket, key: str = "", gate_id: str = ""):
    s = get_settings()
    if key not in (s.device_api_key, s.anpr_api_key):
        await ws.close(code=4401)
        return
    await ws.accept()
    await hub.serve(Client(ws=ws, role="DEVICE", gate_id=gate_id or None))


# ------------------------------------------------------------------ phone app (browser build) at /app/
mimetypes.add_type("application/wasm", ".wasm")
_worker_web = Path(get_settings().worker_web_dist)
if (_worker_web / "index.html").is_file():
    @app.get("/app", include_in_schema=False)
    async def _app_redirect():
        return RedirectResponse("/app/")

    app.mount("/app", StaticFiles(directory=_worker_web, html=True), name="worker_app")

# ------------------------------------------------------------------ dashboard SPA
_dist = Path(get_settings().dashboard_dist)
if (_dist / "index.html").is_file():
    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        f = _dist / path
        if path and f.is_file() and _dist.resolve() in f.resolve().parents:
            return FileResponse(f)
        return FileResponse(_dist / "index.html")
