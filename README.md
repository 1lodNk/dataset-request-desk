# Dataset Request Desk

Internal platform for a robotics data-collection company. Clients request datasets ("200 episodes of a
robot arm picking cups, by date X"); operators fulfil them by assigning recorded episodes; clients accept or
reject the delivery. Python (FastAPI + SQLAlchemy + Alembic) on PostgreSQL, with a small dependency-free web UI.

Design reasoning, trade-offs and known gaps are in **[NOTES.md](NOTES.md)**.

## Run it

```bash
docker compose up --build        # db + migrations + seed users + episode import + API + UI
# open http://localhost:8000      (API docs: http://localhost:8000/docs, health: /health)
```

On first start the container applies migrations, creates the demo users, and imports `seed/episodes.csv`
(the messy file; the import report is printed in the logs). `docker compose down -v` wipes the database.

| Role | Email | Password |
|---|---|---|
| admin | admin@example.com | admin123 |
| operator | ops1@example.com / ops2@example.com | ops123 |
| client (Acme Robotics) | client-a@example.com | client123 |
| client (Beta Labs) | client-b@example.com | client123 |

Passwords are stored as bcrypt hashes. Seeding is opt-in (`SEED_USERS=1`, set only in `docker-compose.yml`), so
a production deployment never gets these accounts by accident.

### Without Docker
```bash
python -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt
export DATABASE_URL=postgresql+psycopg://desk:desk@localhost:5432/desk   # see .env.example
alembic upgrade head && python -m app.cli seed-users && python -m app.cli import-episodes seed/episodes.csv
uvicorn app.main:app --reload
```

## Tests
```bash
pip install -r requirements-dev.txt && pytest          # zero setup: runs on a temp SQLite file (~7 s)
docker compose run --rm tests                           # same suite on PostgreSQL, incl. concurrency tests
TEST_DATABASE_URL=postgresql+psycopg://... pytest       # PostgreSQL without Docker
```
135 tests. The schema is built by running the real Alembic migrations, and one check confirms the models and
migrations haven't drifted. Covered: login/token handling; a role × endpoint matrix and tenant isolation; **every**
status pair (valid and invalid) and which role may take each valid step; assignment rules; import idempotency and
every messy-row category; analytics correctness and range edges; health/logging/error shape; and (PostgreSQL
only) races: simultaneous transitions and simultaneous assignment of one episode have exactly one winner. CI
(`.github/workflows/ci.yml`) runs everything on both databases.
`tools/ui_smoke.mjs` is an optional browser-less end-to-end check of the UI (jsdom) against a running server.

## How it works

**Workflow** (the table `TRANSITIONS` in `app/domain.py` *is* the spec, and tests iterate it):

```
submitted --operator/admin--> in_progress --operator/admin--> delivered --client--> accepted
                                   ^                              |
                                   +------operator/admin----- rejected <--client--+   (rework)
```
Delivery needs ≥ `episodes_requested` assigned episodes. Admins can do everything operators can, but accepting
or rejecting a delivery is the client's decision, so admins can't.

**Assignment rules**: only `good`/`usable` episodes; one request per episode (`UNIQUE` in the database); only
while the request is `in_progress`; batches are all-or-nothing.

**API** (all under `/api`, Bearer JWT; full schema at `/docs`)

| | |
|---|---|
| `POST /auth/login`, `GET /auth/me` | sign in / who am I |
| `GET,POST /requests`, `GET /requests/{id}` | clients see only their own; only clients create |
| `POST /requests/{id}/transitions` | `{to_status, comment}` |
| `GET /requests/{id}/history` | audit trail: who, when, from → to, note |
| `GET,POST /requests/{id}/episodes`, `DELETE …/{episode_id}` | assigned episodes; assign (staff) / unassign (staff) |
| `GET /episodes`, `POST /episodes/import`, `GET /episodes/imports` | staff: browse/filter, CSV import, import history |
| `GET /analytics?from=&to=` | staff: the three reports below |
| `GET,POST,PATCH /users` | admin only |
| `GET /events` | Server-Sent Events: live request changes (**stretch item: real-time**) |
| `GET /health` | 200 only if the database answers |

Errors are JSON `{detail, code, ...}`; `code` is stable and machine-readable (`invalid_transition`,
`role_not_allowed`, `insufficient_episodes`, `episode_already_assigned`, ...).

### The CSV import (`seed/episodes.csv`: 191 rows → 170 imported, 21 skipped)
Idempotent (re-running imports 0), and every row is accounted for: `imported + skipped == total_rows`. The report
lists each skipped row with its line number and reason. Decisions per case:

| Case in the file | Decision |
|---|---|
| ` arm-01`, `Good`, `USABLE`, `  Pick Cup `, `PICK CUP`, `ep-00003` | normalised (trim, lower-case, collapse spaces; ids upper-cased) and imported |
| `2026-08-14 09:12:00`, `…Z`, `14/08/2026 09:15` | parsed. ISO first, else **day-first** `dd/mm/yyyy`; no zone ⇒ **UTC** |
| exact duplicate rows (EP-00030, EP-00074) | first kept, the rest `duplicate_in_file` |
| same id, different data (EP-00011; `EP-00003` vs `ep-00003`) | **neither imported**: `conflicting_duplicate_in_file`. We can't know which is right |
| id already stored with different data | stored copy kept, `conflicts_with_existing` lists the differing fields |
| missing/unknown robot, bad quality (`excellent`, blank), duration blank/`N/A`/`-5`/`45.5`/`999999`, unparseable or future date, missing id, wrong column count, blank lines | skipped, with reason |
| missing operator name | imported with a warning (operator is informational, not needed for assignment) |

CLI: `python -m app.cli import-episodes FILE [--json]`. API: `POST /api/episodes/import` (multipart, 50 MB cap).

### Analytics (`GET /api/analytics?from=2026-09-01&to=2026-09-30`, dates inclusive, UTC)
Done in SQL (`GROUP BY`, `percentile_cont`), never by loading rows into Python:
episodes per day per robot (by `recorded_at`); requests by status (requests *created* in range, all five statuses
always present) and **median submitted→first delivered** (requests first delivered in range; the first delivery is
used, so rework cycles don't change a request's number); top 5 tasks by `good` episodes.

**With 5 million episodes.** Each report is a range scan on an index (`ix_episodes_recorded_robot`,
`ix_episodes_good_task_recorded`, a partial index), never a full-table scan, so cost grows with the *rows in the
range*, not the table. Measured on PostgreSQL 16 with 500,000 episodes (dev laptop-class VM, one run): 7-day
range **48 ms**, full-year range **557 ms**, picker page (filtered, unassigned, newest first) **51 ms**. The
5M figures below are extrapolation, not measurement: the same year-long report touches ~10× the rows, so
expect **seconds**; a week stays well under a second. Next step if that matters: a `daily_episode_counts` rollup
table updated by the import (answers in milliseconds regardless of volume), then partition `episodes` by month.
Lists use `limit+1` for `has_more` instead of `COUNT(*)`.

## Configuration
`DATABASE_URL`, `JWT_SECRET` (≥32 chars; **the app refuses to start in production with the dev default**),
`JWT_TTL_MINUTES` (60), `APP_ENV` (`production` disables `/docs`), `LOG_LEVEL`, `BCRYPT_ROUNDS` (12).
Logs: one JSON line per request on stdout: `method, path, status, duration_ms, user_id, request_id`.

## Layout
```
app/            main.py (wiring, middleware) · domain.py (rules table) · models.py · schemas.py · deps.py (auth)
  routers/      thin HTTP layer          services/  business logic, one transaction per operation
  static/       the UI (index.html, app.js, style.css)       cli.py  seed-users / import-episodes
migrations/     Alembic (0001_initial)    seed/  provided data    tests/  pytest    docs/BRIEF.md  the task
```
