from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_staff
from ..errors import Invalid
from ..models import User
from ..services import analytics

router = APIRouter(prefix="/api/analytics", tags=["analytics (staff)"])


@router.get("")
def get_analytics(
    date_from: date | None = Query(None, alias="from", description="inclusive, UTC; default: 29 days before `to`"),
    date_to: date | None = Query(None, alias="to", description="inclusive, UTC; default: today"),
    _: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    end = date_to or datetime.now(timezone.utc).date()
    start = date_from or end - timedelta(days=29)
    if start > end:
        raise Invalid("'from' must not be after 'to'", code="invalid_range")
    if (end - start).days > analytics.MAX_RANGE_DAYS:
        raise Invalid(f"range too large (max {analytics.MAX_RANGE_DAYS} days)", code="invalid_range")
    return analytics.compute(db, start, end)
