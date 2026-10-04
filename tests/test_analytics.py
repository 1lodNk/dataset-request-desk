"""Analytics endpoint: correctness of the three required reports, range semantics, validation."""
from datetime import datetime, timedelta, timezone

import pytest

from app.models import Episode, Request, RequestStatusEvent

UTC = timezone.utc


def ep(db, eid, robot, task, quality, at):
    db.add(Episode(episode_id=eid, robot_id=robot, task_name=task, recorded_at=at, duration_seconds=30,
                   operator_name="Aline", quality=quality))


def make_request(db, client_user, created, *, status="submitted", delivered_at=(), task="pick cup"):
    r = Request(client_id=client_user.id, task_name=task, episodes_requested=1, deadline=created.date(),
                status=status, created_at=created, updated_at=created)
    db.add(r)
    db.flush()
    db.add(RequestStatusEvent(request_id=r.id, from_status=None, to_status="submitted",
                              changed_by=client_user.id, changed_at=created))
    for t in delivered_at:  # several entries = delivered, rejected, re-delivered ...
        db.add(RequestStatusEvent(request_id=r.id, from_status="in_progress", to_status="delivered",
                                  changed_by=client_user.id, changed_at=t))
    return r


def get(client, H, user, **params):
    r = client.get("/api/analytics", params=params, headers=H(user))
    assert r.status_code == 200, r.text
    return r.json()


def test_episodes_per_day_per_robot_respects_range_boundaries(client, db, users, H):
    ep(db, "E1", "arm-01", "pick cup", "good", datetime(2026, 8, 31, 23, 59, 59, tzinfo=UTC))  # before
    ep(db, "E2", "arm-01", "pick cup", "good", datetime(2026, 9, 1, 0, 0, 0, tzinfo=UTC))      # first instant
    ep(db, "E3", "arm-01", "pick cup", "bad", datetime(2026, 9, 1, 23, 59, 59, tzinfo=UTC))
    ep(db, "E4", "arm-02", "pick cup", "good", datetime(2026, 9, 1, 12, 0, tzinfo=UTC))
    ep(db, "E5", "arm-01", "pick cup", "good", datetime(2026, 9, 2, 0, 0, 0, tzinfo=UTC))      # next day
    ep(db, "E6", "arm-01", "pick cup", "good", datetime(2026, 9, 2, 23, 59, 59, tzinfo=UTC))   # last instant
    ep(db, "E7", "arm-01", "pick cup", "good", datetime(2026, 9, 3, 0, 0, 0, tzinfo=UTC))      # after
    db.commit()
    data = get(client, H, users.ops, **{"from": "2026-09-01", "to": "2026-09-02"})
    assert data["episodes_per_day_per_robot"] == [
        {"day": "2026-09-01", "robot_id": "arm-01", "episodes": 2},
        {"day": "2026-09-01", "robot_id": "arm-02", "episodes": 1},
        {"day": "2026-09-02", "robot_id": "arm-01", "episodes": 2},
    ]


def test_top_five_tasks_counts_only_good_episodes_and_breaks_ties_by_name(client, db, users, H):
    day = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
    n = 0
    plan = {"a": 5, "b": 4, "c": 4, "d": 3, "e": 2, "f": 1, "g": 0}
    for task, good in plan.items():
        for _ in range(good):
            n += 1
            ep(db, f"G{n}", "arm-01", task, "good", day)
    for _ in range(10):  # 'g' has lots of episodes, but none are good -> must not appear
        n += 1
        ep(db, f"X{n}", "arm-01", "g", "bad", day)
        n += 1
        ep(db, f"U{n}", "arm-01", "f", "usable", day)
    ep(db, "OLD", "arm-01", "f", "good", day - timedelta(days=30))  # outside range
    db.commit()
    top = get(client, H, users.ops, **{"from": "2026-09-01", "to": "2026-09-01"})["top_tasks_by_good_episodes"]
    assert top == [{"task_name": t, "good_episodes": c}
                   for t, c in [("a", 5), ("b", 4), ("c", 4), ("d", 3), ("e", 2)]]


def test_requests_by_status_counts_requests_created_in_range(client, db, users, H):
    t = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    for status in ("submitted", "submitted", "in_progress", "accepted"):
        make_request(db, users.a, t, status=status)
    make_request(db, users.b, t - timedelta(days=5), status="rejected")  # created outside range
    db.commit()
    data = get(client, H, users.admin, **{"from": "2026-09-01", "to": "2026-09-01"})
    assert data["requests_by_status"] == {"submitted": 2, "in_progress": 1, "delivered": 0,
                                          "accepted": 1, "rejected": 0}  # every status is always present


def test_median_time_to_deliver(client, db, users, H):
    t0 = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    hours = lambda h: t0 + timedelta(hours=h)  # noqa: E731
    make_request(db, users.a, t0, status="delivered", delivered_at=[hours(2)])      # 2h
    make_request(db, users.a, t0, status="accepted", delivered_at=[hours(6)])       # 6h
    # rework: delivered at 24h, rejected, delivered again at 30h -> the *first* delivery counts: 24h
    make_request(db, users.b, t0, status="accepted", delivered_at=[hours(24), hours(30)])
    make_request(db, users.b, t0, status="in_progress")                              # never delivered
    db.commit()
    window = {"from": "2026-09-01", "to": "2026-09-05"}
    data = get(client, H, users.ops, **window)
    assert data["delivered_requests_in_range"] == 3
    assert data["median_submit_to_deliver_seconds"] == pytest.approx(6 * 3600)       # odd count: the middle one

    make_request(db, users.b, t0, status="delivered", delivered_at=[hours(36)])      # now 4 values
    db.commit()
    data = get(client, H, users.ops, **window)
    assert data["median_submit_to_deliver_seconds"] == pytest.approx((6 + 24) / 2 * 3600)  # even: mean of middle two


def test_median_only_includes_requests_first_delivered_in_range(client, db, users, H):
    t0 = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    make_request(db, users.a, t0, status="delivered", delivered_at=[t0 + timedelta(hours=1)])
    make_request(db, users.a, t0, status="delivered", delivered_at=[t0 + timedelta(days=10)])  # delivered later
    db.commit()
    data = get(client, H, users.ops, **{"from": "2026-09-01", "to": "2026-09-02"})
    assert data["delivered_requests_in_range"] == 1
    assert data["median_submit_to_deliver_seconds"] == pytest.approx(3600)


def test_empty_range_returns_zeroes_not_errors(client, users, H):
    data = get(client, H, users.ops, **{"from": "2020-01-01", "to": "2020-01-31"})
    assert data["episodes_per_day_per_robot"] == [] and data["top_tasks_by_good_episodes"] == []
    assert data["median_submit_to_deliver_seconds"] is None and data["delivered_requests_in_range"] == 0
    assert sum(data["requests_by_status"].values()) == 0


def test_defaults_to_the_last_thirty_days(client, users, H):
    data = get(client, H, users.ops)
    assert (datetime.fromisoformat(data["to"]) - datetime.fromisoformat(data["from"])).days == 29


@pytest.mark.parametrize("params", [{"from": "2026-09-05", "to": "2026-09-01"},
                                    {"from": "2020-01-01", "to": "2026-09-01"},
                                    {"from": "yesterday"}])
def test_invalid_ranges_are_rejected(client, users, H, params):
    assert client.get("/api/analytics", params=params, headers=H(users.ops)).status_code == 422
