"""FastAPI app factory for the cloud relay: `uvicorn relay.main:app` (or `create_app(settings)` in tests)."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.exceptions import HTTPException as FastAPIHTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import payments as pay
from .config import Settings, get_settings
from .db import Base, make_engine, make_sessionmaker
from .gateway import PaymentGateway, make_gateway
from .i18n import LANGS, pick_lang
from .pages import router as pages_router
from .security import CSRF_COOKIE, csrf_token_for
from .sms import SmsSender, make_sms
from .sync import deliver_direct_and_record, kv_get, run_housekeeping
from .sync import router as sync_router

log = logging.getLogger("relay")

CSP = ("default-src 'self'; img-src 'self' data: https://*.razorpay.com https://rzp.io; style-src 'self'; "
       "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


def housekeeping(app: FastAPI) -> None:
    db = app.state.sessionmaker()
    try:
        run_housekeeping(db, app.state.settings.phone_retention_days)
        db.commit()
    finally:
        db.close()


def create_app(settings: Optional[Settings] = None, *, gateway: Optional[PaymentGateway] = None,
               sms: Optional[SmsSender] = None) -> FastAPI:
    s = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        housekeeping(app)
        yield

    app = FastAPI(title="Station Parking - customer relay", version="1.0.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    engine = make_engine(s.database_url)
    Base.metadata.create_all(engine)
    app.state.settings = s
    app.state.engine = engine
    app.state.sessionmaker = make_sessionmaker(engine)
    app.state.gateway = gateway or make_gateway(s)
    app.state.sms = sms or make_sms(s.sms_provider, s.msg91_auth_key, s.msg91_template_otp)

    app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

    @app.middleware("http")
    async def web_middleware(request: Request, call_next):
        machine = request.url.path.startswith(("/sync/", "/webhooks/", "/healthz", "/static/"))
        qlang = request.query_params.get("lang")
        request.state.lang = pick_lang(qlang, request.cookies.get("lang"), request.headers.get("accept-language"))
        if not machine:
            csrf_token_for(request)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if not machine:
            response.headers.setdefault("Content-Security-Policy", CSP)
            response.headers.setdefault("X-Frame-Options", "DENY")
            response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
            response.headers.setdefault("Cache-Control", "no-store")
            tok = getattr(request.state, "csrf", None)
            if tok and request.cookies.get(CSRF_COOKIE) != tok:
                response.set_cookie(CSRF_COOKIE, tok, httponly=True, secure=s.cookie_secure, samesite="lax", path="/")
            if qlang in LANGS and request.cookies.get("lang") != qlang:
                response.set_cookie("lang", qlang, max_age=365 * 86400, secure=s.cookie_secure, samesite="lax",
                                    path="/")
        if s.cookie_secure and not machine:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        path = request.url.path
        if path.startswith(("/sync/", "/webhooks/", "/healthz")) or path.endswith("/status") or \
                path.startswith("/t/"):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        from .web import render

        db = app.state.sessionmaker()
        try:
            msg = exc.detail if isinstance(exc.detail, str) and exc.status_code != 404 else \
                "Page not found."
            return render(request, db, "message.html", {"title_key": "error", "message": msg, "back": "/"},
                          exc.status_code)
        finally:
            db.close()

    # ------------------------------------------------------------------ gateway webhooks
    @app.post("/webhooks/{provider}")
    async def webhook(provider: str, request: Request, bg: BackgroundTasks):
        gw: PaymentGateway = app.state.gateway
        if provider != gw.name:
            raise FastAPIHTTPException(404, "unknown provider")
        body = await request.body()
        headers = {k.lower(): v for k, v in request.headers.items()}
        try:
            events = gw.parse_webhook(body, headers)
        except PermissionError:
            raise FastAPIHTTPException(401, "bad signature")
        except ValueError:
            raise FastAPIHTTPException(400, "bad payload")
        db = app.state.sessionmaker()
        try:
            msgs, handled = [], 0
            for ev in events:
                intent, new = pay.apply_gateway_event(db, ev)
                handled += intent is not None
                msgs += new
            db.commit()
        finally:
            db.close()
        if msgs and s.edge_url:
            bg.add_task(deliver_direct_and_record, app.state, [m.id for m in msgs])
        return {"ok": True, "handled": handled}

    @app.get("/healthz")
    def healthz():
        db = app.state.sessionmaker()
        try:
            return {"ok": True, "last_push_at": kv_get(db, "last_push_at"), "gateway": app.state.gateway.name}
        finally:
            db.close()

    app.include_router(sync_router)
    app.include_router(pages_router)
    return app


def _lazy_app() -> FastAPI:  # pragma: no cover - production entry point
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return create_app()


def __getattr__(name: str):  # `uvicorn relay.main:app` builds the app on first access only
    if name == "app":
        global app
        app = _lazy_app()
        return app
    raise AttributeError(name)
