from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user, require_client, require_staff
from ..models import User
from ..schemas import (
    AssignIn, AssignOut, EpisodeOut, HistoryOut, Page, RequestCreate, RequestOut, Status, TransitionIn,
)
from ..services import assignments, episodes, requests as svc

router = APIRouter(prefix="/api/requests", tags=["requests"])


@router.get("", response_model=Page[RequestOut])
def list_requests(
    status: Status | None = None,
    task_name: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Clients see only their own requests; staff see all."""
    return svc.list_requests(db, user, status=status, task_name=task_name, limit=limit, offset=offset)


@router.post("", response_model=RequestOut, status_code=201)
def create_request(body: RequestCreate, user: User = Depends(require_client), db: Session = Depends(get_db)):
    return svc.create_request(db, user, task_name=body.task_name, episodes_requested=body.episodes_requested,
                              deadline=body.deadline, notes=body.notes)


@router.get("/{request_id}", response_model=RequestOut)
def get_request(request_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return svc.get_request(db, user, request_id)


@router.post("/{request_id}/transitions", response_model=RequestOut)
def transition(request_id: int, body: TransitionIn, user: User = Depends(current_user),
               db: Session = Depends(get_db)):
    """Move a request along its workflow. Which roles may take which step is defined in domain.TRANSITIONS."""
    return svc.transition(db, user, request_id, body.to_status, body.comment)


@router.get("/{request_id}/history", response_model=list[HistoryOut])
def history(request_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return svc.history(db, user, request_id)


@router.get("/{request_id}/episodes", response_model=list[EpisodeOut])
def request_episodes(request_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    svc.get_request_row(db, user, request_id)  # authorisation / 404
    return episodes.episodes_of_request(db, request_id)


@router.post("/{request_id}/episodes", response_model=AssignOut)
def assign_episodes(request_id: int, body: AssignIn, user: User = Depends(require_staff),
                    db: Session = Depends(get_db)):
    return assignments.assign(db, user, request_id, body.episode_ids)


@router.delete("/{request_id}/episodes/{episode_id}")
def unassign_episode(request_id: int, episode_id: str, user: User = Depends(require_staff),
                     db: Session = Depends(get_db)):
    return assignments.unassign(db, user, request_id, episode_id)
