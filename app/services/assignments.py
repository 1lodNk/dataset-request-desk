"""Assigning episodes to requests. The business rules, in the order they are checked:
  1. the request must be 'in_progress' (operators fulfil work that has been picked up);
  2. every episode must exist;
  3. only 'good' / 'usable' episodes can be assigned;
  4. an episode can belong to at most one request (also enforced by UNIQUE in the database).
The whole batch is all-or-nothing, and the request row is locked for the duration so an assignment
can't race with a transition to 'delivered' (which counts assignments)."""
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..domain import ASSIGNABLE_QUALITIES
from ..errors import Conflict, Invalid, NotFound
from ..events import broker
from ..models import Assignment, Episode, User, utcnow
from .requests import get_request_row


def _count(db: Session, request_id: int) -> int:
    return db.execute(
        select(func.count(Assignment.id)).where(Assignment.request_id == request_id)
    ).scalar_one()


def _require_open(req) -> None:
    if req.status != "in_progress":
        raise Conflict(
            f"episodes can only be changed while a request is 'in_progress' (it is '{req.status}')",
            code="request_not_in_progress", status=req.status,
        )


def assign(db: Session, actor: User, request_id: int, episode_ids: list[str]) -> dict:
    req = get_request_row(db, actor, request_id, lock=True)
    _require_open(req)

    episodes = {e.episode_id: e for e in db.scalars(select(Episode).where(Episode.episode_id.in_(episode_ids)))}
    missing = [i for i in episode_ids if i not in episodes]
    if missing:
        raise Invalid("some episodes do not exist", code="unknown_episodes", episode_ids=missing)

    bad_quality = [i for i, e in episodes.items() if e.quality not in ASSIGNABLE_QUALITIES]
    if bad_quality:
        raise Conflict("only 'good' or 'usable' episodes can be assigned", code="episode_not_assignable",
                       episode_ids=bad_quality)

    existing = {
        e_id: r_id
        for e_id, r_id in db.execute(
            select(Episode.episode_id, Assignment.request_id)
            .join(Assignment, Assignment.episode_id == Episode.id)
            .where(Episode.episode_id.in_(episode_ids))
        )
    }
    elsewhere = {e: r for e, r in existing.items() if r != req.id}
    if elsewhere:
        raise Conflict("some episodes are already assigned to another request",
                       code="episode_already_assigned", episodes=elsewhere)

    already_here = [i for i in episode_ids if i in existing]  # re-sending the same ids is a no-op
    to_add = [i for i in episode_ids if i not in existing]
    now = utcnow()
    db.add_all(Assignment(request_id=req.id, episode_id=episodes[i].id, assigned_by=actor.id, assigned_at=now)
               for i in to_add)
    try:
        db.commit()
    except IntegrityError:
        # Another transaction assigned one of these episodes to a different request between our check
        # and our insert (their request-row lock isn't ours). UNIQUE(episode_id) caught it.
        db.rollback()
        raise Conflict("an episode was assigned to another request concurrently; retry",
                       code="episode_already_assigned") from None
    count = _count(db, req.id)
    if to_add:
        broker.publish({"type": "request.assignments_changed", "request_id": req.id,
                        "client_id": req.client_id, "assigned_count": count})
    return {"assigned": to_add, "already_assigned_to_this_request": already_here, "assigned_count": count}


def unassign(db: Session, actor: User, request_id: int, episode_id: str) -> dict:
    req = get_request_row(db, actor, request_id, lock=True)
    _require_open(req)
    ep_pk = db.scalar(select(Episode.id).where(Episode.episode_id == episode_id.strip().upper()))
    assignment_id = None if ep_pk is None else db.scalar(
        select(Assignment.id).where(Assignment.request_id == req.id, Assignment.episode_id == ep_pk)
    )
    if assignment_id is None:
        db.rollback()
        raise NotFound("that episode is not assigned to this request")
    db.execute(delete(Assignment).where(Assignment.id == assignment_id))
    db.commit()
    count = _count(db, req.id)
    broker.publish({"type": "request.assignments_changed", "request_id": req.id,
                    "client_id": req.client_id, "assigned_count": count})
    return {"assigned_count": count}
