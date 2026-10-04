from datetime import date
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from fastapi import HTTPException
from app.models import Assignment, Request, RequestStatusHistory, User

VALID_TRANSITIONS = {
    "submitted": {"in_progress"},
    "in_progress": {"delivered"},
    "delivered": {"accepted", "rejected"},
    "rejected": {"in_progress"},
    "accepted": set(),
}

def create_request(db: Session, client: User, task_name: str, episodes_requested: int, deadline: date, notes: str | None):
    request = Request(client_id=client.id, task_name=task_name.strip(), episodes_requested=episodes_requested, deadline=deadline, notes=notes, status="submitted")
    db.add(request)
    db.flush()
    db.add(RequestStatusHistory(request_id=request.id, from_status=None, to_status="submitted", changed_by=client.id))
    db.commit()
    db.refresh(request)
    return request

def change_status(db: Session, request: Request, actor: User, target: str):
    if target not in VALID_TRANSITIONS.get(request.status, set()):
        raise HTTPException(400, f"Invalid transition: {request.status} -> {target}")
    if actor.role == "client":
        if request.client_id != actor.id or target not in {"accepted", "rejected"} or request.status != "delivered":
            raise HTTPException(403, "Client cannot perform this transition")
    elif actor.role in {"operator", "admin"}:
        if target not in {"in_progress", "delivered"}:
            raise HTTPException(403, "Operator cannot perform this transition")
    else:
        raise HTTPException(403, "Insufficient permissions")
    if target == "delivered":
        count = db.scalar(select(func.count(Assignment.id)).where(Assignment.request_id == request.id)) or 0
        if count < request.episodes_requested:
            raise HTTPException(400, f"Cannot deliver: {count}/{request.episodes_requested} episodes assigned")
    old = request.status
    request.status = target
    db.add(RequestStatusHistory(request_id=request.id, from_status=old, to_status=target, changed_by=actor.id))
    db.commit()
    db.refresh(request)
    return request
