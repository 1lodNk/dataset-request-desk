"""Assignment rules: one request per episode, only good/usable, only while in_progress, all-or-nothing."""
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models import Assignment, Episode
from tests.conftest import IS_SQLITE
from tests.helpers import add_episodes, assign, drive_to, move, new_request


@pytest.fixture
def open_request(client, users, H):
    req = new_request(client, H(users.a), n=3)
    assert move(client, H(users.ops), req["id"], "in_progress").status_code == 200
    return req


def test_good_and_usable_episodes_can_be_assigned(client, db, users, H, open_request):
    good = add_episodes(db, 2, quality="good")
    usable = add_episodes(db, 1, quality="usable", start=10)
    r = assign(client, H(users.ops), open_request["id"], good + usable)
    assert r.status_code == 200 and r.json()["assigned_count"] == 3
    shown = client.get(f"/api/requests/{open_request['id']}/episodes", headers=H(users.a)).json()
    assert {e["episode_id"] for e in shown} == set(good + usable)


def test_bad_episodes_are_refused_and_nothing_is_assigned(client, db, users, H, open_request):
    good = add_episodes(db, 2, quality="good")
    bad = add_episodes(db, 1, quality="bad", start=10)
    r = assign(client, H(users.ops), open_request["id"], good + bad)
    assert r.status_code == 409 and r.json()["code"] == "episode_not_assignable"
    assert r.json()["episode_ids"] == bad
    assert db.scalar(select(func.count(Assignment.id))) == 0  # all-or-nothing: the good ones weren't taken


def test_episode_cannot_belong_to_two_requests(client, db, users, H, open_request):
    other = new_request(client, H(users.b), n=1)
    move(client, H(users.ops), other["id"], "in_progress")
    ids = add_episodes(db, 2)
    assert assign(client, H(users.ops), open_request["id"], ids).status_code == 200
    r = assign(client, H(users.ops), other["id"], [ids[0]])
    assert r.status_code == 409 and r.json()["code"] == "episode_already_assigned"
    assert r.json()["episodes"] == {ids[0]: open_request["id"]}
    assert db.scalar(select(func.count(Assignment.id)).where(Assignment.request_id == other["id"])) == 0


def test_resending_the_same_episodes_is_a_harmless_noop(client, db, users, H, open_request):
    ids = add_episodes(db, 2)
    assign(client, H(users.ops), open_request["id"], ids)
    r = assign(client, H(users.ops), open_request["id"], ids + add_episodes(db, 1, start=20))
    assert r.status_code == 200
    body = r.json()
    assert sorted(body["already_assigned_to_this_request"]) == sorted(ids)
    assert len(body["assigned"]) == 1 and body["assigned_count"] == 3


def test_duplicate_ids_in_one_payload_and_id_casing(client, db, users, H, open_request):
    ids = add_episodes(db, 1)
    r = assign(client, H(users.ops), open_request["id"], [ids[0], ids[0].lower(), f" {ids[0]} "])
    assert r.status_code == 200 and r.json()["assigned_count"] == 1


def test_unknown_episode_ids_are_reported(client, db, users, H, open_request):
    ids = add_episodes(db, 1)
    r = assign(client, H(users.ops), open_request["id"], ids + ["EP-NOPE"])
    assert r.status_code == 422 and r.json()["episode_ids"] == ["EP-NOPE"]


@pytest.mark.parametrize("state", ["submitted", "delivered", "accepted", "rejected"])
def test_assignments_only_change_while_in_progress(client, db, users, H, state):
    req = new_request(client, H(users.a), n=1)
    drive_to(client, db, users, H, req, state)
    spare = add_episodes(db, 1, prefix="SPARE-")
    r = assign(client, H(users.ops), req["id"], spare)
    assert r.status_code == 409 and r.json()["code"] == "request_not_in_progress"
    if state != "submitted":
        held = db.scalars(select(Episode.episode_id).join(Assignment, Assignment.episode_id == Episode.id)
                          .where(Assignment.request_id == req["id"])).first()
        r = client.delete(f"/api/requests/{req['id']}/episodes/{held}", headers=H(users.ops))
        assert r.status_code == 409  # a delivered shipment can't be quietly edited


def test_unassign_frees_the_episode_for_another_request(client, db, users, H, open_request):
    other = new_request(client, H(users.b), n=1)
    move(client, H(users.ops), other["id"], "in_progress")
    ids = add_episodes(db, 1)
    assign(client, H(users.ops), open_request["id"], ids)
    assert client.delete(f"/api/requests/{open_request['id']}/episodes/{ids[0]}",
                         headers=H(users.ops)).json()["assigned_count"] == 0
    assert assign(client, H(users.ops), other["id"], ids).status_code == 200


def test_unassigning_something_not_assigned_is_404(client, db, users, H, open_request):
    ids = add_episodes(db, 1)
    r = client.delete(f"/api/requests/{open_request['id']}/episodes/{ids[0]}", headers=H(users.ops))
    assert r.status_code == 404


def test_database_itself_refuses_a_double_assignment(db, users, client, H, open_request):
    """The API check can race; UNIQUE(episode_id) is the guarantee."""
    other = new_request(client, H(users.b), n=1)
    ids = add_episodes(db, 1)
    ep = db.scalar(select(Episode).where(Episode.episode_id == ids[0]))
    db.add(Assignment(request_id=open_request["id"], episode_id=ep.id, assigned_by=users.ops.id))
    db.commit()
    db.add(Assignment(request_id=other["id"], episode_id=ep.id, assigned_by=users.ops.id))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_episode_list_filters(client, db, users, H, open_request):
    add_episodes(db, 3, task="pick cup", quality="good")
    add_episodes(db, 2, task="fold towel", quality="usable", start=10)
    bad = add_episodes(db, 1, task="pick cup", quality="bad", start=20)
    taken = add_episodes(db, 1, task="pick cup", quality="good", start=30)
    assign(client, H(users.ops), open_request["id"], taken)

    def q(**params):
        r = client.get("/api/episodes", params=params, headers=H(users.ops))
        assert r.status_code == 200
        return r.json()

    assert len(q(task_name="Pick Cup")["items"]) == 5
    assert len(q(task_name="pick cup", quality="good")["items"]) == 4
    assert [e["episode_id"] for e in q(quality="bad")["items"]] == bad
    unassigned = q(task_name="pick cup", quality="good", assignment="unassigned")["items"]
    assert len(unassigned) == 3 and all(e["assigned_request_id"] is None for e in unassigned)
    assert [e["assigned_request_id"] for e in q(assignment="assigned")["items"]] == [open_request["id"]]
    page = q(limit=2)
    assert len(page["items"]) == 2 and page["has_more"] is True
    assert q(limit=200)["has_more"] is False
    assert client.get("/api/episodes?quality=excellent", headers=H(users.ops)).status_code == 422


@pytest.mark.skipif(IS_SQLITE, reason="needs PostgreSQL row locks / real concurrency")
def test_concurrent_assignment_of_one_episode_has_exactly_one_winner(client, db, users, H, monkeypatch):
    import time

    from fastapi.testclient import TestClient

    from app.main import app
    from app.services import assignments as svc

    requests = []
    for _ in range(6):
        r = new_request(client, H(users.a), n=1)
        move(client, H(users.ops), r["id"], "in_progress")
        requests.append(r)
    [episode] = add_episodes(db, 1)
    real_utcnow = svc.utcnow

    def slow_utcnow():  # between the "is it free?" check and the INSERT: all six pass the check
        time.sleep(0.25)
        return real_utcnow()

    monkeypatch.setattr(svc, "utcnow", slow_utcnow)

    def attempt(r):
        with TestClient(app) as c:
            return assign(c, H(users.ops), r["id"], [episode]).status_code

    with ThreadPoolExecutor(6) as pool:
        codes = list(pool.map(attempt, requests))
    # The application-level check can't see the others' uncommitted inserts, so only
    # UNIQUE(assignments.episode_id) stands between us and six assignments of one episode.
    assert sorted(codes) == [200] + [409] * 5
    assert db.scalar(select(func.count(Assignment.id))) == 1
