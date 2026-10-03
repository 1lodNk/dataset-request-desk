from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from ..db import SessionLocal

router = APIRouter(tags=["ops"])


@router.get("/health")
def health():
    """Liveness + readiness in one: 200 only if the database answers. Unauthenticated by design
    (load balancers / docker healthchecks call it) and reveals nothing beyond up/down."""
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse({"status": "degraded", "database": "unreachable"}, status_code=503)
    return {"status": "ok", "database": "ok"}
