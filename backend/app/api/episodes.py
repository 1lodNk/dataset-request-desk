from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import exists, select
from sqlalchemy.orm import Session
from app.auth.dependencies import require_roles, current_user
from app.db.session import get_db
from app.importer.csv_import import import_csv
from app.models import Assignment, Episode, Request, User
from app.schemas.episode import EpisodeOut, ImportResult

router = APIRouter(prefix="/episodes", tags=["episodes"])

@router.get("", response_model=list[EpisodeOut])
def list_episodes(db: Session = Depends(get_db), user: User = Depends(require_roles("operator", "admin")), task_name: str | None = None, quality: str | None = Query(None)):
    stmt = select(Episode).order_by(Episode.recorded_at.desc()).limit(500)
    if task_name: stmt = stmt.where(Episode.task_name == " ".join(task_name.strip().lower().split()))
    if quality: stmt = stmt.where(Episode.quality == quality.lower())
    episodes = db.scalars(stmt).all()
    return [EpisodeOut.model_validate(e).model_copy(update={"assigned_request_id": db.scalar(select(Assignment.request_id).where(Assignment.episode_id == e.id))}) for e in episodes]

@router.post("/import", response_model=ImportResult)
def import_episodes(file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(require_roles("operator", "admin"))):
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(400, "Only CSV files are accepted")
    return import_csv(db, file.file)

@router.post("/{episode_id}/assign/{request_id}", status_code=204)
def assign_episode(episode_id: int, request_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("operator", "admin"))):
    episode = db.get(Episode, episode_id)
    if not episode: raise HTTPException(404, "Episode not found")
    request = db.get(Request, request_id)
    if not request: raise HTTPException(404, "Request not found")
    if episode.quality not in {"good", "usable"}: raise HTTPException(400, "Only good or usable episodes can be assigned")
    if db.scalar(select(exists().where(Assignment.episode_id == episode.id))): raise HTTPException(409, "Episode is already assigned")
    db.add(Assignment(request_id=request.id, episode_id=episode.id))
    db.commit()
