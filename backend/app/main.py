import logging, time
from app.auth.security import decode_access_token
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from app.api import auth, requests, episodes, analytics, users
from app.config import settings
from app.db.session import engine
from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("dataset_desk")
app = FastAPI(title="Dataset Request Desk", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=[x.strip() for x in settings.cors_origins.split(",")], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.middleware("http")
async def request_logging(request: Request, call_next):
    start = time.perf_counter()
    status_code = 500
    user_id = None
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        logger.info("request method=%s path=%s status=%s duration_ms=%s user_id=%s", request.method, request.url.path, status_code, duration_ms, user_id)

@app.get("/health")
def health():
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok"}

app.include_router(auth.router)
app.include_router(requests.router)
app.include_router(episodes.router)
app.include_router(analytics.router)
app.include_router(users.router)
