import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .errors import DomainError
from .events import broker
from .logging_conf import setup_logging
from .routers import analytics, auth, episodes, events, health, requests, users

setup_logging(settings.log_level)
log = logging.getLogger("app.request")
STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    broker.bind_loop(asyncio.get_running_loop())
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Dataset Request Desk",
        version="1.0.0",
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    @app.exception_handler(DomainError)
    async def domain_error_handler(_: Request, exc: DomainError):
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        return JSONResponse({"detail": exc.message, "code": exc.code, **exc.extra},
                            status_code=exc.status_code, headers=headers)

    @app.middleware("http")
    async def access_log_and_security_headers(request: Request, call_next):
        started = time.perf_counter()
        request.state.user_id = None
        request.state.request_id = request_id = (request.headers.get("x-request-id") or uuid.uuid4().hex)[:64]
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled error", extra={"fields": {"request_id": request_id}})
            response = JSONResponse({"detail": "internal server error", "code": "internal_error",
                                     "request_id": request_id}, status_code=500)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        if not request.url.path.startswith(("/docs", "/openapi.json")):
            # The UI loads nothing from other origins and has no inline script/style, so a strict
            # policy costs nothing and blunts XSS. (Swagger UI at /docs needs a CDN, so it is exempt.)
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        log.info("request", extra={"fields": {
            "method": request.method, "path": request.url.path, "status": response.status_code,
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            "user_id": request.state.user_id, "request_id": request_id}})
        return response

    for module in (health, auth, users, requests, episodes, analytics, events):
        app.include_router(module.router)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    return app


app = create_app()
