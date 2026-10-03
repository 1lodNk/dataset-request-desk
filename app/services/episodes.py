from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ..domain import normalize_task_name
from ..models import Assignment, Episode


def _out(e: Episode, request_id: int | None) -> dict:
    return {
        "episode_id": e.episode_id, "robot_id": e.robot_id, "task_name": e.task_name,
        "recorded_at": e.recorded_at, "duration_seconds": e.duration_seconds,
        "operator_name": e.operator_name, "quality": e.quality, "assigned_request_id": request_id,
    }


def list_episodes(
    db: Session, *, task_name: str | None, quality: str | None, robot_id: str | None,
    assignment: str, limit: int, offset: int,
) -> dict:
    """Filtered, paginated episode list. Fetches limit+1 rows to learn `has_more` without a COUNT(*)
    (an exact count over millions of filtered rows is the expensive part of a list endpoint)."""
    stmt = select(Episode, Assignment.request_id).outerjoin(Assignment, Assignment.episode_id == Episode.id)
    if task_name:
        stmt = stmt.where(Episode.task_name == normalize_task_name(task_name))
    if quality:
        stmt = stmt.where(Episode.quality == quality)
    if robot_id:
        stmt = stmt.where(Episode.robot_id == robot_id)
    if assignment == "unassigned":
        stmt = stmt.where(Assignment.id.is_(None))
    elif assignment == "assigned":
        stmt = stmt.where(Assignment.id.is_not(None))
    rows = db.execute(
        stmt.order_by(desc(Episode.recorded_at), desc(Episode.id)).limit(limit + 1).offset(offset)
    ).all()
    return {"items": [_out(e, rid) for e, rid in rows[:limit]], "limit": limit, "offset": offset,
            "has_more": len(rows) > limit}


def episodes_of_request(db: Session, request_id: int) -> list[dict]:
    rows = db.execute(
        select(Episode).join(Assignment, Assignment.episode_id == Episode.id)
        .where(Assignment.request_id == request_id).order_by(Episode.recorded_at, Episode.id)
    ).scalars().all()
    return [_out(e, request_id) for e in rows]
