from datetime import date, datetime, timedelta, timezone

from app.models import Episode


def add_episodes(db, n, *, task="pick cup", quality="good", robot="arm-01", prefix="EP-", start=1,
                 recorded_at=None):
    ids = []
    base = recorded_at or datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    for i in range(start, start + n):
        eid = f"{prefix}{i:05d}"
        db.add(Episode(episode_id=eid, robot_id=robot, task_name=task, recorded_at=base + timedelta(minutes=i),
                       duration_seconds=30, operator_name="Aline", quality=quality))
        ids.append(eid)
    db.commit()
    return ids


def new_request(client, headers, *, task="pick cup", n=2, **extra):
    body = {"task_name": task, "episodes_requested": n,
            "deadline": str(date.today() + timedelta(days=30)), "notes": "", **extra}
    r = client.post("/api/requests", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def move(client, headers, request_id, to_status, comment=None):
    return client.post(f"/api/requests/{request_id}/transitions",
                       json={"to_status": to_status, "comment": comment}, headers=headers)


def assign(client, headers, request_id, ids):
    return client.post(f"/api/requests/{request_id}/episodes", json={"episode_ids": ids}, headers=headers)


def drive_to(client, db, users, H, req, target):
    """Walk a request through *valid* steps until it reaches `target` (assigning episodes as needed)."""
    path = {
        "submitted": [],
        "in_progress": ["in_progress"],
        "delivered": ["in_progress", "delivered"],
        "accepted": ["in_progress", "delivered", "accepted"],
        "rejected": ["in_progress", "delivered", "rejected"],
    }[target]
    for step in path:
        if step == "delivered":
            ids = add_episodes(db, req["episodes_requested"], task=req["task_name"],
                               prefix=f"DRV{req['id']}-", start=1)
            assert assign(client, H(users.ops), req["id"], ids).status_code == 200
        actor = users.a if step in ("accepted", "rejected") else users.ops
        r = move(client, H(actor), req["id"], step)
        assert r.status_code == 200, r.text
