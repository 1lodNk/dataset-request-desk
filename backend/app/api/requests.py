from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.auth.dependencies import current_user, require_roles
from app.db.session import get_db
from app.models import Assignment, Episode, Request, User
from app.schemas.request import RequestCreate, RequestOut, StatusChange
from app.services.requests import change_status, create_request

router = APIRouter(prefix="/requests", tags=["requests"])

def serialize(db, request):
    return RequestOut.model_validate(request).model_copy(update={"assigned_count": db.scalar(select(func.count(Assignment.id)).where(Assignment.request_id == request.id)) or 0})

@router.get("", response_model=list[RequestOut])
def list_requests(db: Session = Depends(get_db), user: User = Depends(current_user), status: str | None = Query(None)):
    stmt = select(Request).order_by(Request.created_at.desc())
    if user.role == "client": stmt = stmt.where(Request.client_id == user.id)
    if status: stmt = stmt.where(Request.status == status)
    return [serialize(db, r) for r in db.scalars(stmt).all()]

@router.post("", response_model=RequestOut, status_code=201)
def create(payload: RequestCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("client"))):
    return serialize(db, create_request(db, user, payload.task_name, payload.episodes_requested, payload.deadline, payload.notes))

@router.get("/{request_id}", response_model=RequestOut)
def get_request(request_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    request = db.get(Request, request_id)
    if not request: raise HTTPException(404, "Request not found")
    if user.role == "client" and request.client_id != user.id: raise HTTPException(404, "Request not found")
    return serialize(db, request)

@router.post("/{request_id}/status", response_model=RequestOut)
def update_status(request_id: int, payload: StatusChange, db: Session = Depends(get_db), user: User = Depends(current_user)):
    request = db.get(Request, request_id)
    if not request: raise HTTPException(404, "Request not found")
    if user.role == "client" and request.client_id != user.id: raise HTTPException(404, "Request not found")
    return serialize(db, change_status(db, request, user, payload.status))

@router.get("/{request_id}/episodes")
def request_episodes(request_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    request = db.get(Request, request_id)
    if not request: raise HTTPException(404, "Request not found")
    if user.role == "client" and request.client_id != user.id: raise HTTPException(404, "Request not found")
    rows = db.scalars(select(Episode).join(Assignment).where(Assignment.request_id == request_id)).all()
    return rows
