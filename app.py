import logging
import os
import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from accounts import router as accounts_router
from database import init_db
from finance import router as finance_router
from operations import router as operations_router
from settings import BASE_DIR, Settings

LOGGER = logging.getLogger(__name__)


def create_app(settings: Settings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app):
        await run_in_threadpool(init_db, settings.database)
        yield

    app = FastAPI(
        title="Zubrify Business Manager",
        lifespan=lifespan,
        openapi_url=None if settings.production else "/openapi.json",
    )
    app.state.settings = settings
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie="zubrify_session",
        max_age=settings.session_seconds,
        same_site="lax",
        https_only=settings.production,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts, www_redirect=False)
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
    app.include_router(accounts_router)
    app.include_router(operations_router)
    app.include_router(finance_router)

    @app.exception_handler(sqlite3.OperationalError)
    async def database_unavailable(_request, exc):
        if getattr(exc, "sqlite_errorcode", 0) & 255 in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
            return JSONResponse(
                {"detail": "Database is busy. Retry the same form."}, 503, {"Retry-After": "1"}
            )
        LOGGER.error("Database operation failed (%s)", getattr(exc, "sqlite_errorname", "unknown"))
        return JSONResponse({"detail": "Database operation failed"}, 500)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if not request.url.path.startswith("/static/"):
            response.headers.setdefault("Cache-Control", "no-store")
        if settings.production:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response

    return app


app = create_app(Settings.from_env())

if __name__ == "__main__":
    import uvicorn

    os.umask(0o077)
    settings = app.state.settings
    uvicorn.run(
        app,
        host="0.0.0.0" if settings.production else "127.0.0.1",
        port=int(os.getenv("PORT", "8000")),
        proxy_headers=bool(settings.trusted_proxies),
        forwarded_allow_ips=settings.trusted_proxies,
    )
