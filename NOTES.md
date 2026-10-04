# NOTES

## 1. Design

**Data model** (PostgreSQL; constraints live in the database, not just in Python):
```
users ──< requests ──< request_status_history (append-only audit: from, to, by, at, comment)
             │
             └──< assignments >── episodes >── robots          import_runs ──< episodes (provenance)
                  UNIQUE(episode_id)
```
State lives in PostgreSQL only; the API process is stateless, apart from the in-memory SSE fan-out
(see §5). `requests.status` is the current state; `request_status_history` is the audit trail and the source for the
delivery-time analytics. Roles/qualities/statuses are `CHECK` constraints (not PG enums, which are painful to
migrate). Times are `timestamptz`, always UTC.

**Hardest decisions**
1. *Conflicting duplicates in the import.* Same `episode_id`, different data (e.g. `good` vs `bad`). "First wins" is
   simple but can silently hand a client a bad episode as good; "last wins" is no better. I quarantine **both** and report
   them: a human must pick. Cost: two passes over the file (validate+fingerprint, then insert), which also made
   batching and per-batch commits easy. Existing DB rows with different data are never overwritten either.
2. *Where to enforce concurrency rules.* "One request per episode" is `UNIQUE(assignments.episode_id)`: it holds with
   any number of API processes, and the app-level check exists only to give a friendly message. Status changes and
   assignments lock the request row (`SELECT … FOR UPDATE`) so two simultaneous transitions can't both pass the
   status check, and assigning can't race the "enough episodes?" check on delivery. Both are covered by tests.
3. *Ambiguous analytics definitions.* I chose: requests are bucketed by **creation** date, median time uses the
   **first** `delivered` event (so rework doesn't distort it) and is counted in the range of that delivery. Written down in
   README and in the code docstring. Roles: accepting/rejecting is client-only, even for admins; only clients create requests.

Other ambiguities I decided: task names are normalised (trim/lower/collapse) so filters match; dd/mm/yyyy is read day-first and
zoneless timestamps as UTC (the file has no zone); `duration` must be 1–86,400 s; assigning doesn't require the episode's task to equal
the request's task (the picker pre-filters by it, but staff may knowingly substitute); a request can have *more* episodes
than requested; unassign/assign is only allowed while `in_progress`, so a delivered shipment can't change underneath the client.

## 2. Left out / simplified, and next steps
Left out: refresh tokens and server-side token revocation (deactivating a user *does* take effect immediately, because the
user is re-read on every request); login rate-limiting; password reset/change UI; request cancellation and deadline
enforcement/reminders; keyset pagination; frontend unit tests (the UI is covered only by a jsdom smoke script);
multi-worker real-time; the video files themselves. Robots are reference data seeded by the migration (a new robot needs a migration).
The compose stack was written and its steps run individually (migrate, seed, import, serve, health), but I couldn't run Docker in my build environment, so `docker compose up` itself is untested.

With two more days: (1) bulk import via `COPY` into a staging table + `INSERT … ON CONFLICT` (now ~2.9k rows/s: 500k in ~173 s,
correct but slow); (2) login throttling and httpOnly-cookie sessions with CSRF protection; (3) daily-rollup table for
analytics; (4) PostgreSQL `LISTEN/NOTIFY` for events; (5) request cancel/withdraw state; (6) Playwright tests.

## 3. Something that went wrong
My first concurrency test **passed even with the locking removed**, so it proved nothing. Eight threads fired the same
transition, but each request finishes in a few milliseconds, so they rarely overlapped. I only noticed because I ran a
mutation check (deleted `with_for_update()`, re-ran: still green; 3 times). Diagnosis: the race window (between the status
check and the commit) was far smaller than thread start-up jitter. Fix: the test now sleeps 250 ms inside that window
(by wrapping `utcnow`, which runs there), which makes the race deterministic. Result: without the lock it fails
(six 200s instead of one), with it passes. I repeated the mutation for assignments: there the *database* constraint alone
stops the double assignment (the 409 comes from the `IntegrityError` path), which is exactly the guarantee I wanted to demonstrate.
Smaller ones: this FastAPI version nests routers, so my "every route requires auth" test found zero routes until I
walked the OpenAPI table instead; and my first UI smoke run showed login discarding deep links.

## 4. Security
Passwords: bcrypt (cost 12), bytes-length checked (bcrypt's 72-byte limit is rejected, never truncated), and an identical
error + equalised timing for wrong password vs unknown email. Tokens: HS256 JWT, 60 min, algorithm pinned on decode (no `alg: none`),
`exp`/`sub` required, secret must be ≥32 chars in production or the app won't boot; the user row is loaded on every request,
so role changes and deactivation apply instantly. Authorisation is server-side (dependencies per route; a test walks the OpenAPI
table and fails if any `/api` route works without a token); other clients' requests return 404, not 403. Input: pydantic
validation everywhere, ORM-parameterised SQL (no string-built queries), upload size cap, CSV treated as untrusted. Browser: strict CSP,
`nosniff`, no framing, and the UI only writes server text with `textContent` (a test in the UI smoke injects `<img onerror>` and checks it stays text).

The two things I worry about most:
1. **Token theft via XSS / no revocation.** The JWT lives in `sessionStorage` and is valid for an hour. CSP + `textContent`
   make XSS hard, but one slip means account takeover (worst for an admin token). Fix: httpOnly+SameSite cookie, short access token + rotating refresh, CSRF token.
2. **Credential stuffing / brute force on `/auth/login`.** bcrypt slows each guess but there is no throttle or lockout, and the demo seed
   accounts have known passwords (so seeding is off unless `SEED_USERS=1`). Fix: per-IP+account rate limiting, lockout/backoff, alerting on failures, MFA for admins.

## 5. Scale
*10× users:* the first limit is database connections and the threadpool (sync endpoints; pool of 10+10), then bcrypt CPU on login
bursts. Run several uvicorn workers/containers behind PgBouncer. The SSE broker is in-process, so with multiple workers each would only see its own
events: move to `LISTEN/NOTIFY` (the `publish()` interface is the seam). Row locks are per request, so hot rows are unlikely.
*100× episodes (≈50M):* offset pagination in the picker gets slower with depth (→ keyset on `(recorded_at, id)`);
the `NOT EXISTS`/anti-join for "unassigned" and the full-year analytics (measured ~0.56 s at 500k, expect ~10× at 5M) become the
slow queries (→ rollup table, monthly partitions by `recorded_at`, possibly BRIN). Import is the other limit (see §2); the first-pass `dict` of ids costs
memory (433 MB peak at 500k rows): switch to a staging table and let PostgreSQL do the de-duplication.

## 6. AI tooling
This project was built with Claude (Anthropic) as a pair-programmer