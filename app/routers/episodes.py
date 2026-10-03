import tempfile
from typing import Literal

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_staff
from ..errors import PayloadTooLarge
from ..models import ImportRun, User
from ..schemas import EpisodePage, Quality
from ..services import episodes, importer

router = APIRouter(prefix="/api/episodes", tags=["episodes (staff)"])
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


@router.get("", response_model=EpisodePage)
def list_episodes(
    task_name: str | None = None,
    quality: Quality | None = None,
    robot_id: str | None = None,
    assignment: Literal["any", "unassigned", "assigned"] = "any",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    return episodes.list_episodes(db, task_name=task_name, quality=quality, robot_id=robot_id,
                                  assignment=assignment, limit=limit, offset=offset)


@router.post("/import")
def import_csv(file: UploadFile = File(...), user: User = Depends(require_staff), db: Session = Depends(get_db)):
    """Import a CSV export. Safe to repeat: re-uploading the same file imports nothing new.
    The response says what was imported and what was skipped, and why."""
    with tempfile.TemporaryFile() as tmp:  # spool to disk with a size cap; the importer reads it twice
        size = 0
        while chunk := file.file.read(1 << 20):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                raise PayloadTooLarge(f"file exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
            tmp.write(chunk)
        return importer.import_episodes(db, tmp, filename=file.filename or "upload.csv", user_id=user.id)


@router.get("/imports")
def list_imports(limit: int = Query(20, ge=1, le=100), _: User = Depends(require_staff),
                 db: Session = Depends(get_db)):
    runs = db.scalars(select(ImportRun).order_by(desc(ImportRun.id)).limit(limit))
    return [
        {"id": r.id, "filename": r.filename, "started_at": r.started_at, "finished_at": r.finished_at,
         "total_rows": r.total_rows, "imported": r.imported, "skipped": r.skipped,
         "skipped_by_reason": (r.report or {}).get("skipped_by_reason", {})}
        for r in runs
    ]
