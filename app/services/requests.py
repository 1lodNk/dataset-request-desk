"""Dataset requests: creation, listing, and the status workflow (the heart of the domain rules)."""
from datetime import date

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from ..domain import TRANSITIONS, normalize_task_name
from ..errors import Conflict, Forbidden, InvalidTransition, NotFound
from ..events import broker
from ..models import Assignment, Request, RequestStatusEvent, User, utcnow


def _assigned_count():
    return (
        select(func.count(Assignment.id))
        .where(Assignment.request_id == Request.id)
        .correlate(Request)
        .scalar_subquery()
    )


def allowed_transitions(status: str, role: str) -> list[str]:
    return [to for (frm, to), roles in TRANSITIONS.items() if frm == status and role in roles]


def to_out(req: Request, client_name: str, client_org: str | None, assigned: int, actor: User) -> dict:
    return {
        "id": req.id,
        "client_id": req.client_id,
        "client_name": client_name,
        "client_organisation": client_org,
        "task_name": req.task_name,
        "episodes_requested": req.episodes_requested,
        "assigned_count": assigned,
        "deadline": req.deadline,
        "notes": req.notes,
        "status": req.status,
        "created_at": req.created_at,
        "updated_at": req.updated_at,
        "allowed_transitions": allowed_transitions(req.status, actor.role),
    }


def get_request_row(db: Session, actor: User, request_id: int, *, lock: bool = False) -> Request:
    """Load a request the actor is allowed to see. A client asking for someone else's request gets
    404, not 403: we don't confirm that the id exists. `lock=True` takes a row lock (SELECT ... FOR
    UPDATE on PostgreSQL) so concurrent transitions/assignments on one request are serialised."""
    stmt = select(Request).where(Request.id == request_id)
    if lock:
        stmt = stmt.with_for_update()
    req = db.scalar(stmt)
    if req is None or (actor.role == "client" and req.client_id != actor.id):
        raise NotFound("request not found")
    return req


def create_request(
    db: Session, actor: User, *, task_name: str, episodes_requested: int, deadline: date, notes: str
) -> dict:
    now = utcnow()
    req = Request(
        client_id=actor.id,
        task_name=normalize_task_name(task_name),
        episodes_requested=episodes_requested,
        deadline=deadline,
        notes=notes.strip(),
        status="submitted",
        created_at=now,
        updated_at=now,
    )
    db.add(req)
    db.flush()
    db.add(RequestStatusEvent(request_id=req.id, from_status=None, to_status="submitted",
                              changed_by=actor.id, changed_at=now))
    db.commit()
    broker.publish({"type": "request.created", "request_id": req.id, "client_id": req.client_id,
                    "status": req.status, "task_name": req.task_name})
    return to_out(req, actor.name, actor.organisation, 0, actor)


def _select_with_meta():
    return select(Request, User.name, User.organisation, _assigned_count()).join(
        User, User.id == Request.client_id
    )


def get_request(db: Session, actor: User, request_id: int) -> dict:
    get_request_row(db, actor, request_id)  # authorisation / 404
    req, name, org, assigned = db.execute(_select_with_meta().where(Request.id == request_id)).one()
    return to_out(req, name, org, assigned, actor)


def list_requests(
    db: Session, actor: User, *, status: str | None, task_name: str | None, limit: int, offset: int
) -> dict:
    conds = []
    if actor.role == "client":
        conds.append(Request.client_id == actor.id)  # authorisation is part of the query itself
    if status:
        conds.append(Request.status == status)
    if task_name:
        conds.append(Request.task_name == normalize_task_name(task_name))
    total = db.scalar(select(func.count(Request.id)).where(*conds))
    rows = db.execute(
        _select_with_meta().where(*conds).order_by(desc(Request.created_at), desc(Request.id))
        .limit(limit).offset(offset)
    ).all()
    return {"items": [to_out(r, n, o, a, actor) for r, n, o, a in rows],
            "total": total, "limit": limit, "offset": offset}


def transition(db: Session, actor: User, request_id: int, to_status: str, comment: str | None) -> dict:
    req = get_request_row(db, actor, request_id, lock=True)
    owners = TRANSITIONS.get((req.status, to_status))
    if owners is None:
        raise InvalidTransition(
            f"cannot move a request from '{req.status}' to '{to_status}'",
            from_status=req.status, to_status=to_status,
            allowed=[to for (frm, to) in TRANSITIONS if frm == req.status],
        )
    if actor.role not in owners:
        raise Forbidden(
            f"role '{actor.role}' may not move a request from '{req.status}' to '{to_status}'",
            code="role_not_allowed",
        )
    if to_status == "delivered":
        assigned = db.execute(
            select(func.count(Assignment.id)).where(Assignment.request_id == req.id)
        ).scalar_one()
        if assigned < req.episodes_requested:
            raise Conflict(
                f"request needs {req.episodes_requested} episodes but only {assigned} are assigned",
                code="insufficient_episodes", required=req.episodes_requested, assigned=assigned,
            )
    now = utcnow()
    from_status, req.status, req.updated_at = req.status, to_status, now
    db.add(RequestStatusEvent(request_id=req.id, from_status=from_status, to_status=to_status,
                              changed_by=actor.id, changed_at=now, comment=(comment or "").strip() or None))
    db.commit()
    broker.publish({"type": "request.status_changed", "request_id": req.id, "client_id": req.client_id,
                    "from_status": from_status, "status": to_status, "by": actor.name})
    return get_request(db, actor, request_id)


def history(db: Session, actor: User, request_id: int) -> list[dict]:
    get_request_row(db, actor, request_id)
    rows = db.execute(
        select(RequestStatusEvent, User.name, User.role)
        .join(User, User.id == RequestStatusEvent.changed_by)
        .where(RequestStatusEvent.request_id == request_id)
        .order_by(RequestStatusEvent.changed_at, RequestStatusEvent.id)
    ).all()
    return [
        {"from_status": e.from_status, "to_status": e.to_status, "changed_by_id": e.changed_by,
         "changed_by_name": name, "changed_by_role": role, "changed_at": e.changed_at, "comment": e.comment}
        for e, name, role in rows
    ]
