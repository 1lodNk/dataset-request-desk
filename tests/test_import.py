"""CSV import: idempotency, reporting, normalisation, conflict handling."""
import io
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.models import Episode, ImportRun
from app.services.importer import parse_datetime
from tests.conftest import IS_SQLITE, ROOT

SEED_CSV = (ROOT / "seed" / "episodes.csv").read_bytes()
HEADER = "episode_id,robot_id,task_name,recorded_at,duration_seconds,operator_name,quality\n"


def upload(client, headers, content: bytes, name="episodes.csv"):
    return client.post("/api/episodes/import", files={"file": (name, io.BytesIO(content), "text/csv")},
                       headers=headers)


def csv_bytes(*rows: str, header=HEADER) -> bytes:
    return (header + "".join(r + "\n" for r in rows)).encode()


def count(db):
    return db.scalar(select(func.count(Episode.id)))


# ---- the real messy export ------------------------------------------------------------------
EXPECTED_SKIPS = {
    "blank_row": 2, "malformed_row": 1, "missing_episode_id": 1, "missing_robot_id": 1, "unknown_robot": 1,
    "invalid_quality": 2, "invalid_duration": 5, "invalid_recorded_at": 1, "recorded_at_in_future": 1,
    "duplicate_in_file": 2, "conflicting_duplicate_in_file": 4,
}


def test_seed_file_report_accounts_for_every_row(client, db, users, H):
    r = upload(client, H(users.ops), SEED_CSV)
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["total_rows"] == 191
    assert rep["imported"] == 170 and rep["skipped"] == 21
    assert rep["imported"] + rep["skipped"] == rep["total_rows"]  # nothing vanishes
    assert rep["skipped_by_reason"] == EXPECTED_SKIPS
    assert rep["warnings"] == {"missing_operator_name": 1}
    assert len(rep["skipped_rows"]) == 21 and rep["skipped_rows_truncated"] is False
    assert all(row["reason"] and row["line"] > 1 for row in rep["skipped_rows"])
    assert count(db) == 170


def test_reimporting_the_same_file_changes_nothing(client, db, users, H):
    first = upload(client, H(users.ops), SEED_CSV).json()
    before = {e.episode_id: (e.robot_id, e.task_name, e.recorded_at, e.quality) for e in db.scalars(select(Episode))}
    second = upload(client, H(users.ops), SEED_CSV).json()
    third = upload(client, H(users.admin), SEED_CSV).json()
    assert first["imported"] == 170
    for rep in (second, third):
        assert rep["imported"] == 0 and rep["total_rows"] == 191
        assert rep["skipped_by_reason"]["already_imported"] == 170
        assert {k: v for k, v in rep["skipped_by_reason"].items() if k != "already_imported"} == EXPECTED_SKIPS
    db.expire_all()
    assert count(db) == 170
    assert before == {e.episode_id: (e.robot_id, e.task_name, e.recorded_at, e.quality) for e in db.scalars(select(Episode))}
    assert db.scalar(select(func.count(ImportRun.id))) == 3  # every run is on record


def test_harmless_inconsistencies_are_normalised(client, db, users, H):
    upload(client, H(users.ops), SEED_CSV)
    get = lambda eid: db.scalar(select(Episode).where(Episode.episode_id == eid))  # noqa: E731
    assert get("EP-00008").robot_id == "arm-01"                       # ' arm-01'
    assert get("EP-00009").quality == "good"                          # 'Good'
    assert get("EP-00010").quality == "usable"                        # 'USABLE'
    assert get("EP-00014").recorded_at == datetime(2026, 8, 14, 9, 15, tzinfo=timezone.utc)  # 14/08/2026 09:15
    assert get("EP-00013").recorded_at == datetime(2026, 8, 14, 9, 12, tzinfo=timezone.utc)  # space separator
    tasks = {e.task_name for e in db.scalars(select(Episode))}
    assert "pick cup" in tasks and "  Pick Cup " not in tasks and "PICK CUP" not in tasks
    assert get("EP-90005").operator_name is None                      # imported, with a warning


def test_bad_rows_are_not_imported(client, db, users, H):
    upload(client, H(users.ops), SEED_CSV)
    stored = set(db.scalars(select(Episode.episode_id)))
    for bad in ("EP-00018", "EP-00019", "EP-00020", "EP-00016", "EP-00017", "EP-00025", "EP-00023",
                "EP-00024", "EP-00021", "EP-90001", "EP-90003", "EP-90004"):
        assert bad not in stored, bad


def test_exact_duplicates_import_once_but_conflicting_duplicates_import_never(client, db, users, H):
    upload(client, H(users.ops), SEED_CSV)
    stored = set(db.scalars(select(Episode.episode_id)))
    assert {"EP-00030", "EP-00074"} <= stored  # identical copies: keep one
    # same id, different data (EP-00011 good vs bad; 'ep-00003' vs 'EP-00003' differ entirely):
    # we can't know which is right, so neither is imported and both are reported.
    assert "EP-00011" not in stored and "EP-00003" not in stored
    assert db.scalar(select(func.count(Episode.id)).where(Episode.episode_id == "EP-00030")) == 1


def test_existing_row_with_different_data_is_kept_and_reported(client, db, users, H):
    upload(client, H(users.ops), csv_bytes("EP-1,arm-01,pick cup,2026-09-01T10:00:00,30,Aline,good"))
    r = upload(client, H(users.ops), csv_bytes("EP-1,arm-01,pick cup,2026-09-01T10:00:00,30,Aline,bad",
                                               "EP-2,arm-01,pick cup,2026-09-01T10:05:00,30,Aline,good")).json()
    assert r["imported"] == 1 and r["skipped_by_reason"] == {"conflicts_with_existing": 1}
    assert "quality" in r["skipped_rows"][0]["detail"]
    assert db.scalar(select(Episode.quality).where(Episode.episode_id == "EP-1")) == "good"  # not overwritten


# ---- batching, shape tolerance --------------------------------------------------------------
def test_large_file_across_batches_and_partial_overlap(client, db, users, H):
    rows = [f"EP-{i},arm-02,wipe table,2026-09-01T{(i % 24):02d}:{(i % 60):02d}:00,{10 + i % 50},Eric,good"
            for i in range(1, 1301)]
    r = upload(client, H(users.ops), csv_bytes(*rows)).json()
    assert r["imported"] == 1300 and count(db) == 1300
    more = [f"EP-{i},arm-02,wipe table,2026-09-01T{(i % 24):02d}:{(i % 60):02d}:00,{10 + i % 50},Eric,good"
            for i in range(1001, 1801)]  # 300 already there, 500 new
    r = upload(client, H(users.ops), csv_bytes(*more)).json()
    assert (r["imported"], r["skipped_by_reason"]) == (500, {"already_imported": 300})
    assert count(db) == 1800


def test_columns_may_be_reordered_and_file_may_have_bom_and_crlf(client, db, users, H):
    content = ("\ufeffquality,episode_id,task_name,recorded_at,duration_seconds,operator_name,robot_id\r\n"
               "good,EP-7,Pour Water,2026-09-01T10:00:00Z,20,Kevin,mobile-01\r\n").encode("utf-8")
    r = upload(client, H(users.ops), content)
    assert r.status_code == 200 and r.json()["imported"] == 1
    e = db.scalar(select(Episode).where(Episode.episode_id == "EP-7"))
    assert (e.robot_id, e.task_name, e.quality) == ("mobile-01", "pour water", "good")


@pytest.mark.parametrize("content,needle", [
    (b"", "empty"),
    (b"episode_id,robot_id\nEP-1,arm-01\n", "missing required column"),
    (b"\xff\xfe\x00bad bytes\x80\x81", "UTF-8"),
])
def test_unusable_files_are_rejected_up_front(client, db, users, H, content, needle):
    r = upload(client, H(users.ops), content)
    assert r.status_code == 422 and r.json()["code"] == "invalid_csv" and needle in r.json()["detail"]
    assert count(db) == 0 and db.scalar(select(func.count(ImportRun.id))) == 0


def test_only_staff_can_import(client, users, H):
    assert upload(client, H(users.a), SEED_CSV).status_code == 403
    assert client.post("/api/episodes/import", files={"file": ("x.csv", b"a", "text/csv")}).status_code == 401


def test_import_history_is_listed(client, users, H):
    upload(client, H(users.ops), SEED_CSV, name="export-2026-09.csv")
    runs = client.get("/api/episodes/imports", headers=H(users.ops)).json()
    assert runs[0]["filename"] == "export-2026-09.csv" and runs[0]["imported"] == 170


@pytest.mark.skipif(IS_SQLITE, reason="needs PostgreSQL for real concurrent writers")
def test_two_imports_running_at_once_create_no_duplicates(client, db, users, H):
    from fastapi.testclient import TestClient

    from app.main import app

    def run(_):
        with TestClient(app) as c:
            return upload(c, H(users.ops), SEED_CSV).json()

    with ThreadPoolExecutor(2) as pool:
        reports = list(pool.map(run, range(2)))
    assert count(db) == 170
    assert sum(r["imported"] for r in reports) == 170  # each row was inserted by exactly one of them


# ---- unit tests -----------------------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("2026-08-16T23:28:00", datetime(2026, 8, 16, 23, 28, tzinfo=timezone.utc)),
    ("2026-08-16 23:28:00", datetime(2026, 8, 16, 23, 28, tzinfo=timezone.utc)),
    ("2026-08-16T23:28:00Z", datetime(2026, 8, 16, 23, 28, tzinfo=timezone.utc)),
    ("2026-08-16T23:28:00+02:00", datetime(2026, 8, 16, 21, 28, tzinfo=timezone.utc)),
    ("14/08/2026 09:15", datetime(2026, 8, 14, 9, 15, tzinfo=timezone.utc)),  # day first
    ("14/08/2026", datetime(2026, 8, 14, tzinfo=timezone.utc)),
])
def test_parse_datetime_formats(raw, expected):
    assert parse_datetime(raw) == expected


@pytest.mark.parametrize("raw", ["not a date", "", "31/02/2026 10:00", "2026-13-01T00:00:00", "13/13/2026"])
def test_parse_datetime_rejects_garbage(raw):
    with pytest.raises(ValueError):
        parse_datetime(raw)


def test_seed_users_command_is_idempotent_and_hashes_passwords(client, db):
    from app.cli import seed_users
    from app.models import User

    path = ROOT / "seed" / "users.json"
    seed_users(path)
    seed_users(path)
    assert db.scalar(select(func.count(User.id))) == 5
    for email, pw in (("admin@example.com", "admin123"), ("client-b@example.com", "client123")):
        assert client.post("/api/auth/login", json={"email": email, "password": pw}).status_code == 200
    assert not any(u.password_hash == "admin123" for u in db.scalars(select(User)))
