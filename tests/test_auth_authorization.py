"""Authentication and role-based authorisation. All of it is enforced on the server."""
import re
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.main import app
from tests.helpers import add_episodes, assign, move, new_request


# ---- authentication -------------------------------------------------------------------------
def test_login_returns_token_and_user(client, users):
    r = client.post("/api/auth/login", json={"email": "OPS@t.io", "password": "password123"})  # case-insensitive
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["role"] == "operator" and "password" not in str(body)
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.json()["email"] == "ops@t.io"


def test_login_failures_are_indistinguishable(client, users):
    wrong_pw = client.post("/api/auth/login", json={"email": "ops@t.io", "password": "nope"})
    no_user = client.post("/api/auth/login", json={"email": "ghost@t.io", "password": "nope"})
    assert wrong_pw.status_code == no_user.status_code == 401
    assert wrong_pw.json() == no_user.json()  # no user enumeration


def test_passwords_are_hashed_not_stored(db, users):
    from app.models import User
    from sqlalchemy import select

    hashes = db.scalars(select(User.password_hash)).all()
    assert hashes and all(h.startswith("$2") and "password123" not in h for h in hashes)


def test_every_api_route_requires_authentication(client):
    """Walks the real route table (via OpenAPI), so a newly added endpoint that forgets auth fails here."""
    public = {("post", "/api/auth/login")}
    checked = 0
    for path, methods in app.openapi()["paths"].items():
        if not path.startswith("/api/"):
            continue
        concrete = re.sub(r"\{[^}]+\}", "1", path)
        for method in methods:
            if (method, path) in public:
                continue
            r = client.request(method.upper(), concrete, json={})
            assert r.status_code == 401, f"{method.upper()} {path} -> {r.status_code}"
            checked += 1
    assert checked >= 15


@pytest.mark.parametrize("token_kind", ["garbage", "wrong_secret", "expired", "alg_none"])
def test_bad_tokens_rejected(client, users, token_kind):
    now = datetime.now(timezone.utc)
    claims = {"sub": str(users.admin.id), "iat": int(now.timestamp()),
              "exp": int((now + timedelta(minutes=5)).timestamp())}
    token = {
        "garbage": "not.a.jwt",
        "wrong_secret": jwt.encode(claims, "x" * 40, algorithm="HS256"),
        "expired": jwt.encode({**claims, "exp": int((now - timedelta(minutes=1)).timestamp())},
                              "test-secret-test-secret-test-secret-123456", algorithm="HS256"),
        "alg_none": jwt.encode(claims, None, algorithm="none"),
    }[token_kind]
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_deactivation_takes_effect_immediately_for_existing_tokens(client, users, H):
    token_headers = H(users.ops)
    assert client.get("/api/auth/me", headers=token_headers).status_code == 200
    r = client.patch(f"/api/users/{users.ops.id}", json={"is_active": False}, headers=H(users.admin))
    assert r.status_code == 200
    assert client.get("/api/auth/me", headers=token_headers).status_code == 401
    assert client.post("/api/auth/login", json={"email": "ops@t.io", "password": "password123"}).status_code == 401


def test_role_change_takes_effect_immediately(client, users, H):
    ops_headers = H(users.ops)
    assert client.get("/api/users", headers=ops_headers).status_code == 403
    client.patch(f"/api/users/{users.ops.id}", json={"role": "admin"}, headers=H(users.admin))
    assert client.get("/api/users", headers=ops_headers).status_code == 200  # same token, new role


# ---- role matrix ----------------------------------------------------------------------------
STAFF_ONLY = [("GET", "/api/episodes"), ("GET", "/api/episodes/imports"), ("GET", "/api/analytics")]
ADMIN_ONLY = [("GET", "/api/users"), ("POST", "/api/users"), ("PATCH", "/api/users/1")]


@pytest.mark.parametrize("method,path", STAFF_ONLY)
def test_clients_cannot_use_staff_endpoints(client, users, H, method, path):
    assert client.request(method, path, headers=H(users.a)).status_code == 403


@pytest.mark.parametrize("method,path", ADMIN_ONLY)
@pytest.mark.parametrize("who", ["a", "ops"])
def test_only_admins_manage_users(client, users, H, method, path, who):
    r = client.request(method, path, json={}, headers=H(getattr(users, who)))
    assert r.status_code == 403


def test_staff_endpoints_allow_operator_and_admin(client, users, H):
    for u in (users.ops, users.admin):
        assert client.get("/api/episodes", headers=H(u)).status_code == 200
        assert client.get("/api/analytics", headers=H(u)).status_code == 200


def test_only_clients_create_requests(client, users, H):
    body = {"task_name": "x", "episodes_requested": 1, "deadline": "2099-01-01"}
    assert client.post("/api/requests", json=body, headers=H(users.ops)).status_code == 403
    assert client.post("/api/requests", json=body, headers=H(users.admin)).status_code == 403
    assert client.post("/api/requests", json=body, headers=H(users.a)).status_code == 201


def test_clients_cannot_assign_or_unassign_episodes(client, db, users, H):
    req = new_request(client, H(users.a))
    ids = add_episodes(db, 1)
    assert assign(client, H(users.a), req["id"], ids).status_code == 403
    assert client.delete(f"/api/requests/{req['id']}/episodes/{ids[0]}", headers=H(users.a)).status_code == 403


# ---- tenant isolation -----------------------------------------------------------------------
def test_client_sees_only_own_requests(client, users, H):
    mine = new_request(client, H(users.a))
    theirs = new_request(client, H(users.b))
    listed = client.get("/api/requests", headers=H(users.a)).json()
    assert [r["id"] for r in listed["items"]] == [mine["id"]] and listed["total"] == 1
    staff_view = client.get("/api/requests", headers=H(users.ops)).json()
    assert {r["id"] for r in staff_view["items"]} == {mine["id"], theirs["id"]}


def test_client_cannot_touch_another_clients_request(client, db, users, H):
    theirs = new_request(client, H(users.b))
    rid = theirs["id"]
    h = H(users.a)
    # 404, not 403: don't even confirm the id exists
    assert client.get(f"/api/requests/{rid}", headers=h).status_code == 404
    assert client.get(f"/api/requests/{rid}/history", headers=h).status_code == 404
    assert client.get(f"/api/requests/{rid}/episodes", headers=h).status_code == 404
    assert move(client, h, rid, "accepted").status_code == 404
    assert client.get(f"/api/requests/{rid}", headers=H(users.b)).status_code == 200


def test_filter_params_cannot_widen_a_clients_scope(client, users, H):
    new_request(client, H(users.b))
    r = client.get("/api/requests?status=submitted&limit=200", headers=H(users.a))
    assert r.json()["items"] == []


# ---- user management ------------------------------------------------------------------------
def test_admin_creates_user_and_duplicates_conflict(client, users, H):
    body = {"email": "New@T.io", "name": "New", "role": "operator", "password": "longenough1"}
    r = client.post("/api/users", json=body, headers=H(users.admin))
    assert r.status_code == 201 and r.json()["email"] == "new@t.io"
    assert client.post("/api/users", json=body, headers=H(users.admin)).status_code == 409
    assert client.post("/api/auth/login", json={"email": "new@t.io", "password": "longenough1"}).status_code == 200


@pytest.mark.parametrize("bad", [{"password": "short"}, {"email": "not-an-email"}, {"role": "root"},
                                 {"password": "x" * 80}])
def test_user_creation_validates_input(client, users, H, bad):
    body = {"email": "v@t.io", "name": "V", "role": "client", "password": "longenough1", **bad}
    assert client.post("/api/users", json=body, headers=H(users.admin)).status_code == 422


def test_admin_cannot_lock_themselves_out(client, users, H):
    h = H(users.admin)
    assert client.patch(f"/api/users/{users.admin.id}", json={"is_active": False}, headers=h).status_code == 403
    assert client.patch(f"/api/users/{users.admin.id}", json={"role": "client"}, headers=h).status_code == 403
    assert client.patch(f"/api/users/{users.admin.id}", json={"name": "Renamed"}, headers=h).status_code == 200
