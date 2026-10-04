"""Operability: health endpoint, structured access logs, error shape, security headers, event routing."""
import asyncio
import logging

from app.events import Broker


def request_records(caplog):
    return [r for r in caplog.records if r.name == "app.request" and r.getMessage() == "request"]


def test_health_is_public_and_checks_the_database(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok", "database": "ok"}


def test_health_reports_503_when_the_database_is_down(client, monkeypatch):
    from app.routers import health

    class Broken:
        def __enter__(self):
            raise RuntimeError("db down")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(health, "SessionLocal", Broken)
    r = client.get("/health")
    assert r.status_code == 503 and r.json()["status"] == "degraded"
    assert "db down" not in r.text  # no internals leaked


def test_one_structured_log_line_per_request_with_user_id(client, users, H, caplog):
    caplog.set_level(logging.INFO, logger="app.request")
    client.get("/health")
    client.get("/api/auth/me", headers=H(users.ops))
    client.get("/api/auth/me")  # unauthenticated -> 401
    client.get("/api/requests/999", headers=H(users.a))  # 404
    recs = request_records(caplog)
    assert len(recs) == 4
    fields = [r.fields for r in recs]
    assert [(f["method"], f["path"], f["status"]) for f in fields] == [
        ("GET", "/health", 200), ("GET", "/api/auth/me", 200), ("GET", "/api/auth/me", 401),
        ("GET", "/api/requests/999", 404)]
    assert [f["user_id"] for f in fields] == [None, users.ops.id, None, users.a.id]
    assert all(isinstance(f["duration_ms"], float) and f["request_id"] for f in fields)


def test_login_log_line_carries_the_user_id(client, users, caplog):
    caplog.set_level(logging.INFO, logger="app.request")
    client.post("/api/auth/login", json={"email": "ops@t.io", "password": "password123"})
    assert request_records(caplog)[-1].fields["user_id"] == users.ops.id


def test_log_formatter_emits_one_json_object_per_record():
    import json

    from app.logging_conf import JsonFormatter

    rec = logging.LogRecord("app.request", logging.INFO, __file__, 1, "request", None, None)
    rec.fields = {"method": "GET", "status": 200}
    parsed = json.loads(JsonFormatter().format(rec))
    assert parsed["msg"] == "request" and parsed["method"] == "GET" and parsed["status"] == 200 and "ts" in parsed


def test_request_id_is_echoed_or_generated(client):
    assert client.get("/health", headers={"X-Request-ID": "abc-123"}).headers["x-request-id"] == "abc-123"
    assert len(client.get("/health").headers["x-request-id"]) >= 16


def test_unhandled_errors_return_clean_500_and_are_logged(client, users, H, monkeypatch, caplog):
    from app.services import requests as svc

    monkeypatch.setattr(svc, "list_requests", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("secret boom")))
    r = client.get("/api/requests", headers=H(users.ops))
    assert r.status_code == 500
    body = r.json()
    assert body["code"] == "internal_error" and "secret boom" not in r.text and "Traceback" not in r.text
    assert any(rec.exc_info for rec in caplog.records)  # the stack trace is in the logs, not the response
    assert request_records(caplog)[-1].fields["status"] == 500


def test_security_headers(client):
    for path in ("/", "/health"):
        h = client.get(path).headers
        assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY"
        assert "default-src 'self'" in h["content-security-policy"]


def test_validation_errors_are_422_and_unknown_routes_404(client, users, H):
    assert client.post("/api/requests", json={"task_name": 5}, headers=H(users.a)).status_code == 422
    assert client.get("/api/nope").status_code == 404


def test_static_ui_is_served_without_auth(client):
    r = client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert client.get("/static/app.js").status_code == 200


# ---- live events ----------------------------------------------------------------------------
def test_event_stream_requires_authentication(client):
    assert client.get("/api/events").status_code == 401
    assert client.get("/api/events", headers={"Authorization": "Bearer junk"}).status_code == 401


def test_broker_routes_events_by_role():
    async def scenario():
        broker = Broker()
        broker.bind_loop(asyncio.get_running_loop())
        staff = broker.subscribe(1, "operator")
        mine = broker.subscribe(10, "client")
        theirs = broker.subscribe(11, "client")
        broker.publish({"type": "request.created", "request_id": 5, "client_id": 10})
        await asyncio.sleep(0.05)
        return staff.queue.qsize(), mine.queue.qsize(), theirs.queue.qsize()

    assert asyncio.run(scenario()) == (1, 1, 0)  # a client never sees another client's events


def test_broker_is_safe_without_subscribers_or_loop():
    Broker().publish({"type": "x"})  # no loop bound, nobody listening: must not raise
