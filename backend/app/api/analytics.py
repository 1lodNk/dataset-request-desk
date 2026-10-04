from datetime import date, timedelta
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from app.auth.dependencies import require_roles
from app.db.session import get_db
from app.models import User
from app.schemas.analytics import AnalyticsOut
from app.services.analytics import analytics

router = APIRouter(prefix="/analytics", tags=["analytics"])

@router.get("", response_model=AnalyticsOut)
def get_analytics(start_date: date = Query(...), end_date: date = Query(...), db: Session = Depends(get_db), user: User = Depends(require_roles("operator", "admin"))):
    if end_date <= start_date: raise HTTPException(400, "end_date must be after start_date")
    return analytics(db, start_date, end_date)
