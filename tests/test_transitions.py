"""The request workflow:  submitted -> in_progress -> delivered -> accepted | rejected -> in_progress."""
import itertools
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select

from app.domain import STATUSES, TRANSITIONS
from app.models import Request, RequestStatusEvent
from tests.conftest import IS_SQLITE
from tests.helpers import add_episodes, assign, drive_to, move, new_request


def test_happy_path_records_who_and_when(client, db, users, H):
    req = new_request(client, H(users.a), n=2)
    assert req["status"] == "submitted"
    assert move(client, H(users.ops), req["id"], "in_progress", "picking this up").status_code == 200
    ids = add_episodes(db, 2)
    assert assign(client, H(users.ops), req["id"], ids).status_code == 200
    assert move(client, H(users.ops2), req["id"], "delivered").status_code == 200
    r = move(client, H(users.a), req["id"], "accepted", "looks great")
    assert r.status_code == 200 and r.json()["status"] == "accepted"

    hist = client.get(f"/api/requests/{req['id']}/history", headers=H(users.a)).json()
    assert [(h["from_status"], h["to_status"]) for h in hist] == [
        (None, "submitted"), ("submitted", "in_progress"), ("in_progress", "delivered"),
        ("delivered", "accepted")]
    assert [h["changed_by_name"] for h in hist] == ["Client A", "Olu Operator", "Odile Operator", "Client A"]
    assert [h["changed_by_role"] for h in hist] == ["client", "operator", "operator", "client"]
    assert hist[1]["comment"] == "picking this up" and hist[-1]["comment"] == "looks great"
    assert all(h["changed_at"] for h in hist)


def test_rejection_loops_back_into_rework_then_acceptance(client, db, users, H):
    req = new_request(client, H(users.a), n=2)
    drive_to(client, db, users, H, req, "rejected")
    assert move(client, H(users.ops), req["id"], "in_progress", "swapping 1 episode").status_code == 200
    assert move(client, H(users.ops), req["id"], "delivered").status_code == 200  # episodes were kept
    assert move(client, H(users.a), req["id"], "accepted").status_code == 200
    trail = [h["to_status"] for h in client.get(f"/api/requests/{req['id']}/history", headers=H(users.a)).json()]
    assert trail == ["submitted", "in_progress", "delivered", "rejected", "in_progress", "delivered", "accepted"]


ALL_PAIRS = list(itertools.product(STATUSES, STATUSES))
INVALID_PAIRS = [(a, b) for a, b in ALL_PAIRS if (a, b) not in TRANSITIONS]


@pytest.mark.parametrize("frm,to", INVALID_PAIRS)
def test_every_invalid_transition_is_rejected_and_leaves_no_trace(client, db, users, H, frm, to):
    req = new_request(client, H(users.a), n=1)
    drive_to(client, db, users, H, req, frm)
    before = db.scalar(select(func.count(RequestStatusEvent.id)))
    # try as every role: no role may take an edge that doesn't exist
    for actor in (users.a, users.ops, users.admin):
        r = move(client, H(actor), req["id"], to)
        assert r.status_code == 409 and r.json()["code"] == "invalid_transition", (actor.role, r.text)
    db.expire_all()
    assert db.get(Request, req["id"]).status == frm
    assert db.scalar(select(func.count(RequestStatusEvent.id))) == before


def test_there_are_exactly_five_valid_edges():
    assert len(TRANSITIONS) == 5 and len(INVALID_PAIRS) == 25 - 5


@pytest.mark.parametrize(("frm", "to", "allowed", "denied"), [
    ("submitted", "in_progress", ["ops", "admin"], ["a"]),
    ("in_progress", "delivered", ["ops", "admin"], ["a"]),
    ("delivered", "accepted", ["a"], ["ops", "admin"]),
    ("delivered", "rejected", ["a"], ["ops", "admin"]),
    ("rejected", "in_progress", ["ops", "admin"], ["a"]),
])
def test_each_step_belongs_to_the_right_role(client, db, users, H, frm, to, allowed, denied):
    for who in denied:
        req = new_request(client, H(users.a), n=1)
        drive_to(client, db, users, H, req, frm)
        r = move(client, H(getattr(users, who)), req["id"], to)
        assert r.status_code == 403 and r.json()["code"] == "role_not_allowed", (who, r.text)
        db.expire_all()
        assert db.get(Request, req["id"]).status == frm  # unchanged
    for who in allowed:
        req = new_request(client, H(users.a), n=1)
        drive_to(client, db, users, H, req, frm)
        if to == "delivered":  # enough episodes were assigned by drive_to's helper only on the way in
            ids = add_episodes(db, 1, prefix=f"X{req['id']}-")
            assign(client, H(users.ops), req["id"], ids)
        assert move(client, H(getattr(users, who)), req["id"], to).status_code == 200


def test_cannot_deliver_without_enough_episodes(client, db, users, H):
    req = new_request(client, H(users.a), n=3)
    move(client, H(users.ops), req["id"], "in_progress")
    assign(client, H(users.ops), req["id"], add_episodes(db, 2))
    r = move(client, H(users.ops), req["id"], "delivered")
    assert r.status_code == 409
    assert (r.json()["code"], r.json()["required"], r.json()["assigned"]) == ("insufficient_episodes", 3, 2)
    assert client.get(f"/api/requests/{req['id']}", headers=H(users.a)).json()["status"] == "in_progress"
    assign(client, H(users.ops), req["id"], add_episodes(db, 1, start=50))
    assert move(client, H(users.ops), req["id"], "delivered").status_code == 200


def test_unassigning_below_the_requested_count_blocks_redelivery(client, db, users, H):
    req = new_request(client, H(users.a), n=2)
    move(client, H(users.ops), req["id"], "in_progress")
    ids = add_episodes(db, 2)
    assign(client, H(users.ops), req["id"], ids)
    client.delete(f"/api/requests/{req['id']}/episodes/{ids[0]}", headers=H(users.ops))
    assert move(client, H(users.ops), req["id"], "delivered").status_code == 409


def test_unknown_status_value_is_a_validation_error(client, users, H):
    req = new_request(client, H(users.a))
    r = client.post(f"/api/requests/{req['id']}/transitions", json={"to_status": "teleported"},
                    headers=H(users.ops))
    assert r.status_code == 422


def test_allowed_transitions_hint_matches_the_callers_role(client, users, H):
    req = new_request(client, H(users.a))
    assert req["allowed_transitions"] == []  # a client can't do anything with a 'submitted' request
    staff = client.get(f"/api/requests/{req['id']}", headers=H(users.ops)).json()
    assert staff["allowed_transitions"] == ["in_progress"]


@pytest.mark.parametrize("bad", [{"episodes_requested": 0}, {"episodes_requested": -3},
                                 {"episodes_requested": 10**7}, {"task_name": "   "},
                                 {"deadline": "2000-01-01"}, {"deadline": "soon"}])
def test_request_creation_validates_input(client, users, H, bad):
    body = {"task_name": "pick cup", "episodes_requested": 5, "deadline": "2099-01-01", **bad}
    assert client.post("/api/requests", json=body, headers=H(users.a)).status_code == 422


def test_task_name_is_normalised_so_filters_line_up(client, users, H):
    req = new_request(client, H(users.a), task="  Pick   CUP ")
    assert req["task_name"] == "pick cup"
    assert client.get("/api/requests?task_name=PICK cup", headers=H(users.ops)).json()["total"] == 1


# ---- concurrency (needs real row locks => PostgreSQL) ---------------------------------------
@pytest.mark.skipif(IS_SQLITE, reason="row-level locking is a PostgreSQL feature; run with TEST_DATABASE_URL")
def test_concurrent_identical_transitions_only_one_wins(client, users, H, monkeypatch):
    import time

    from fastapi.testclient import TestClient

    from app.main import app
    from app.services import requests as svc

    req = new_request(client, H(users.a))
    real_utcnow = svc.utcnow

    def slow_utcnow():  # runs between the status check and the commit: makes the race window huge
        time.sleep(0.25)
        return real_utcnow()

    monkeypatch.setattr(svc, "utcnow", slow_utcnow)

    def attempt(who):
        with TestClient(app) as c:
            return move(c, H(who), req["id"], "in_progress").status_code

    with ThreadPoolExecutor(6) as pool:
        codes = list(pool.map(attempt, [users.ops, users.ops2] * 3))
    # Without the row lock every thread passes the 'submitted' check and all six succeed.
    assert sorted(codes) == [200] + [409] * 5
    hist = client.get(f"/api/requests/{req['id']}/history", headers=H(users.ops)).json()
    assert [h["to_status"] for h in hist] == ["submitted", "in_progress"]  # exactly one audit row
