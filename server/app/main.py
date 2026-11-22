"""Intenda backend application entry point.

One deployable web service: FastAPI serves /api/v1/*, the WebSocket endpoint,
SSE streams, and (in production) the built React frontend from the same origin.
"""
from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .config import settings
from .db import SessionLocal, init_db
from .deps import CSRF_EXEMPT_SUFFIXES, CSRF_HEADER, SESSION_COOKIE
from .errors import ApiError, api_error_handler, error_body
from .models import Session as SessionRow
from .security import sha256_hex
from .ws import manager

logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))
log = logging.getLogger("intenda")


def create_app() -> FastAPI:
    app = FastAPI(title="Intenda API", version="1.0.0")
    app.add_exception_handler(ApiError, api_error_handler)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        details = []
        for err in exc.errors():
            details.append({
                "loc": [str(x) for x in err.get("loc", [])],
                "msg": err.get("msg", ""),
                "type": err.get("type", ""),
            })
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "VALIDATION_ERROR",
                               "message": "The request could not be processed.",
                               "details": {"fields": details},
                               "request_id": str(uuid.uuid4())}},
        )

    @app.exception_handler(404)
    async def not_found_handler(request: Request, exc):
        if request.url.path.startswith("/api/"):
            return JSONResponse(
                status_code=404,
                content={"error": {"code": "NOT_FOUND", "message": "Resource not found.",
                                   "details": {}, "request_id": str(uuid.uuid4())}},
            )
        return JSONResponse(status_code=404, content={"detail": "Not Found"})

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = str(uuid.uuid4())
        response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; script-src 'self' https://accounts.google.com; "
            "frame-src 'self' https://accounts.google.com; connect-src 'self' ws: wss: https://accounts.google.com"
        )
        return response

    @app.middleware("http")
    async def csrf_guard(request: Request, call_next):
        """CSRF protection for cookie-authenticated mutations (spec 7.7)."""
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path.startswith("/api/v1"):
            if not request.url.path.endswith(CSRF_EXEMPT_SUFFIXES):
                token = request.cookies.get(SESSION_COOKIE)
                header = request.headers.get(CSRF_HEADER)
                valid = False
                if token and header:
                    db = SessionLocal()
                    try:
                        row = (
                            db.query(SessionRow)
                            .filter(SessionRow.session_hash == sha256_hex(token),
                                    SessionRow.revoked_at.is_(None))
                            .first()
                        )
                        valid = row is not None and row.csrf_token_hash == sha256_hex(header)
                    finally:
                        db.close()
                if not valid:
                    err = ApiError(403, "CSRF_FAILED", "CSRF token is missing or invalid.")
                    return JSONResponse(status_code=403, content=error_body(request, err))
        return await call_next(request)

    from .routes import agent as agent_routes
    from .routes import audit as audit_routes
    from .routes import auth as auth_routes
    from .routes import automations as automations_routes
    from .routes import briefing as briefing_routes
    from .routes import confirmations as confirmations_routes
    from .routes import mail as mail_routes
    from .routes import onboarding as onboarding_routes
    from .routes import review as review_routes
    from .routes import settings as settings_routes
    from .routes import tasks as tasks_routes
    from .routes import ws as ws_routes

    app.include_router(auth_routes.router)
    app.include_router(onboarding_routes.router)
    app.include_router(mail_routes.router)
    app.include_router(briefing_routes.router)
    app.include_router(agent_routes.router)
    app.include_router(tasks_routes.router)
    app.include_router(confirmations_routes.router)
    app.include_router(audit_routes.router)
    app.include_router(review_routes.router)
    app.include_router(automations_routes.router)
    app.include_router(settings_routes.router)
    app.include_router(ws_routes.router)

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "intenda"}

    @app.get("/api/v1/health")
    def api_health():
        return {"data": {"status": "ok", "service": "intenda"}, "meta": {}}

    @app.on_event("startup")
    async def startup() -> None:
        init_db()
        manager.set_loop(asyncio.get_running_loop())
        from .services.runner import runner_loop

        app.state.runner_task = asyncio.create_task(runner_loop())
        log.info("intenda backend ready (env=%s, model=%s, llm_configured=%s)",
                 settings.APP_ENV, settings.LLM_MODEL, bool(settings.LLM_API_KEY))

    # --- production: serve the built frontend (dist/) with SPA fallback ---
    from pathlib import Path

    from fastapi.responses import FileResponse

    dist_dir = Path(settings.FRONTEND_DIST)
    if dist_dir.is_dir():
        index_file = dist_dir / "index.html"

        @app.get("/{full_path:path}")
        def spa(full_path: str):
            if full_path.startswith("api/"):
                raise ApiError(404, "NOT_FOUND", "The requested resource was not found.")
            candidate = (dist_dir / full_path).resolve()
            if full_path and candidate.is_file() and dist_dir.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(index_file)

    return app


app = create_app()
