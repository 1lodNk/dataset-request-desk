"""Idempotent, fully-reported episode import from the recording system's messy CSV export.

Design (see NOTES.md for the reasoning):
  * Nothing is silently dropped or silently "fixed" beyond harmless normalisation (case, whitespace,
    date format). Every row ends up either imported or skipped-with-a-reason, and
    imported + skipped == total_rows always holds.
  * Idempotent: the business key `episode_id` is UNIQUE. A re-run finds the rows already stored and
    reports them as `already_imported`; inserts use ON CONFLICT DO NOTHING so two imports running at
    once can't create duplicates either.
  * Conflicting duplicates (same id, different data) are quarantined, not guessed: we can't know which
    row is right, and importing the wrong one would hand a client bad data.
  * Two streaming passes over the file (validate+fingerprint, then insert in batches), so memory is
    proportional to the number of distinct ids, not the file size; progress is committed per batch.
"""
import csv
import hashlib
import io
import re
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, BinaryIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain import QUALITIES, normalize_task_name
from ..errors import Invalid
from ..models import Episode, ImportRun, Robot, utcnow

REQUIRED_COLUMNS = ("episode_id", "robot_id", "task_name", "recorded_at", "duration_seconds",
                    "operator_name", "quality")
BATCH_SIZE = 500
MAX_DETAIL_ROWS = 500  # per-row details kept in the report (counts are always complete)
MAX_DURATION_SECONDS = 86_400
ID_RE = re.compile(r"^EP-\d+$")
# ISO-8601 is tried first; dd/mm/yyyy (day first) is the only other format seen in the export.
LEGACY_DATE_FORMATS = ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y")


class RowRejected(Exception):
    def __init__(self, reason: str, detail: str | None = None):
        self.reason, self.detail = reason, detail


@dataclass(frozen=True)
class Clean:
    episode_id: str
    robot_id: str
    task_name: str
    recorded_at: datetime
    duration_seconds: int
    operator_name: str | None
    quality: str

    def fingerprint(self) -> tuple:
        return (self.episode_id, self.robot_id, self.task_name, self.recorded_at,
                self.duration_seconds, self.operator_name, self.quality)


def parse_datetime(value: str) -> datetime:
    value = value.strip()
    dt = None
    try:
        dt = datetime.fromisoformat(value)  # handles 'T' or ' ' separator and a trailing 'Z'
    except ValueError:
        for fmt in LEGACY_DATE_FORMATS:
            try:
                dt = datetime.strptime(value, fmt)
                break
            except ValueError:
                continue
    if dt is None:
        raise ValueError(value)
    if dt.tzinfo is None:  # the export carries no zone; we document and assume UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_cells(cells: list[str], idx: dict[str, int], ncols: int, known_robots: set[str],
                now: datetime) -> tuple[Clean, bool, list[str]]:
    """Validate and normalise one CSV row. Returns (clean row, was_normalised, warnings)."""
    if not any(c.strip() for c in cells):
        raise RowRejected("blank_row")
    if len(cells) != ncols:
        raise RowRejected("malformed_row", f"expected {ncols} columns, got {len(cells)}")

    def get(name: str) -> str:
        return cells[idx[name]]

    eid_raw = get("episode_id").strip()
    if not eid_raw:
        raise RowRejected("missing_episode_id")
    eid = eid_raw.upper()
    if not ID_RE.match(eid):
        raise RowRejected("invalid_episode_id", f"{eid_raw!r} does not look like EP-<digits>")

    robot_raw = get("robot_id")
    robot = robot_raw.strip().lower()
    if not robot:
        raise RowRejected("missing_robot_id")
    if robot not in known_robots:
        raise RowRejected("unknown_robot", f"{robot!r} is not a known robot")

    task_raw = get("task_name")
    task = normalize_task_name(task_raw)
    if not task:
        raise RowRejected("missing_task_name")
    if len(task) > 200:
        raise RowRejected("invalid_task_name", "longer than 200 characters")

    rec_raw = get("recorded_at").strip()
    if not rec_raw:
        raise RowRejected("missing_recorded_at")
    try:
        rec = parse_datetime(rec_raw)
    except ValueError:
        raise RowRejected("invalid_recorded_at", f"cannot parse {rec_raw!r}") from None
    if rec > now + timedelta(days=1):
        raise RowRejected("recorded_at_in_future", f"{rec_raw!r} is in the future")

    dur_raw = get("duration_seconds").strip()
    if not dur_raw:
        raise RowRejected("invalid_duration", "missing")
    if not re.fullmatch(r"\d+", dur_raw):
        raise RowRejected("invalid_duration", f"{dur_raw!r} is not a positive whole number of seconds")
    duration = int(dur_raw)
    if not 0 < duration <= MAX_DURATION_SECONDS:
        raise RowRejected("invalid_duration", f"{duration} is outside 1..{MAX_DURATION_SECONDS}")

    quality_raw = get("quality")
    quality = quality_raw.strip().lower()
    if quality not in QUALITIES:
        raise RowRejected("invalid_quality", f"{quality_raw.strip()!r} is not one of {', '.join(QUALITIES)}"
                          if quality else "missing")

    operator = get("operator_name").strip() or None

    normalised = (
        eid != eid_raw or robot != robot_raw or task != task_raw or quality != quality_raw
        or rec_raw != rec.replace(tzinfo=None).isoformat()
    )
    warnings = ["missing_operator_name"] if operator is None else []
    return Clean(eid, robot, task, rec, duration, operator, quality), normalised, warnings


def _differences(existing: Episode, new: Clean) -> list[str]:
    pairs = (("robot_id", existing.robot_id, new.robot_id), ("task_name", existing.task_name, new.task_name),
             ("recorded_at", existing.recorded_at, new.recorded_at),
             ("duration_seconds", existing.duration_seconds, new.duration_seconds),
             ("operator_name", existing.operator_name, new.operator_name),
             ("quality", existing.quality, new.quality))
    return [name for name, old, cur in pairs if old != cur]


class Report:
    def __init__(self) -> None:
        self.total = 0
        self.imported = 0
        self.skipped_by_reason: Counter = Counter()
        self.warnings: Counter = Counter()
        self.normalised_rows = 0
        self.details: list[dict] = []

    @property
    def skipped(self) -> int:
        return sum(self.skipped_by_reason.values())

    def skip(self, line: int, episode_id: str | None, reason: str, detail: str | None = None) -> None:
        self.skipped_by_reason[reason] += 1
        if len(self.details) < MAX_DETAIL_ROWS:
            self.details.append({"line": line, "episode_id": episode_id or None, "reason": reason,
                                 "detail": detail})

    def as_dict(self) -> dict:
        return {
            "total_rows": self.total,
            "imported": self.imported,
            "skipped": self.skipped,
            "skipped_by_reason": dict(sorted(self.skipped_by_reason.items())),
            "warnings": dict(self.warnings),
            "normalised_rows": self.normalised_rows,
            "skipped_rows": self.details,
            "skipped_rows_truncated": self.skipped > len(self.details),
        }


@contextmanager
def _text(binary: BinaryIO):
    binary.seek(0)
    wrapper = io.TextIOWrapper(binary, encoding="utf-8-sig", newline="")
    try:
        yield wrapper
    finally:
        wrapper.detach()  # don't let the wrapper close the caller's file


def _header_index(header: list[str] | None) -> tuple[dict[str, int], int]:
    if not header:
        raise Invalid("the CSV file is empty", code="invalid_csv")
    names = [h.strip().lower() for h in header]
    missing = [c for c in REQUIRED_COLUMNS if c not in names]
    if missing:
        raise Invalid(f"missing required column(s): {', '.join(missing)}", code="invalid_csv")
    return {n: names.index(n) for n in REQUIRED_COLUMNS}, len(header)


def _sha256(binary: BinaryIO) -> str:
    binary.seek(0)
    h = hashlib.sha256()
    for chunk in iter(lambda: binary.read(1 << 20), b""):
        h.update(chunk)
    return h.hexdigest()


def _insert_ignore(db: Session) -> Any:
    if db.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        return pg_insert(Episode.__table__)
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    return sqlite_insert(Episode.__table__)


def _flush(db: Session, batch: list, run: ImportRun, report: Report) -> None:
    if not batch:
        return
    existing = {
        e.episode_id: e
        for e in db.scalars(select(Episode).where(Episode.episode_id.in_([b[1].episode_id for b in batch])))
    }
    fresh = []
    for line, clean, normalised, warnings in batch:
        stored = existing.get(clean.episode_id)
        if stored is None:
            fresh.append((line, clean, normalised, warnings))
        elif diffs := _differences(stored, clean):
            report.skip(line, clean.episode_id, "conflicts_with_existing",
                        "differs from the stored episode in: " + ", ".join(diffs) + " (stored copy kept)")
        else:
            report.skip(line, clean.episode_id, "already_imported")
    if fresh:
        now = utcnow()
        rows = [
            {"episode_id": c.episode_id, "robot_id": c.robot_id, "task_name": c.task_name,
             "recorded_at": c.recorded_at, "duration_seconds": c.duration_seconds,
             "operator_name": c.operator_name, "quality": c.quality, "created_at": now,
             "import_run_id": run.id}
            for _, c, _, _ in fresh
        ]
        stmt = _insert_ignore(db).values(rows).on_conflict_do_nothing(index_elements=["episode_id"]) \
            .returning(Episode.__table__.c.episode_id)
        inserted = set(db.scalars(stmt))
        for line, clean, normalised, warnings in fresh:
            if clean.episode_id in inserted:
                report.imported += 1
                report.normalised_rows += normalised
                report.warnings.update(warnings)
            else:  # a concurrent import won the race for this id
                report.skip(line, clean.episode_id, "already_imported", "inserted concurrently")
    db.commit()  # per-batch commit: a crash keeps progress, and re-running is safe by design


def import_episodes(db: Session, fileobj: BinaryIO, *, filename: str, user_id: int | None) -> dict:
    known_robots = set(db.scalars(select(Robot.robot_id)))
    now = utcnow()

    # ---- pass 1: validate the file shape, fingerprint every valid row to find conflicting duplicates
    valid_lines: dict[str, list[int]] = {}
    fingerprints: dict[str, tuple] = {}
    conflicts: set[str] = set()
    try:
        with _text(fileobj) as f:
            reader = csv.reader(f)
            idx, ncols = _header_index(next(reader, None))
            for cells in reader:
                try:
                    clean, _, _ = parse_cells(cells, idx, ncols, known_robots, now)
                except RowRejected:
                    continue
                valid_lines.setdefault(clean.episode_id, []).append(reader.line_num)
                if fingerprints.setdefault(clean.episode_id, clean.fingerprint()) != clean.fingerprint():
                    conflicts.add(clean.episode_id)
    except UnicodeDecodeError:
        raise Invalid("the file is not valid UTF-8 text", code="invalid_csv") from None
    except csv.Error as exc:
        raise Invalid(f"the file is not valid CSV: {exc}", code="invalid_csv") from None

    run = ImportRun(filename=filename[:255], sha256=_sha256(fileobj), started_by=user_id, started_at=now,
                    total_rows=0, imported=0, skipped=0)
    db.add(run)
    db.commit()

    # ---- pass 2: classify every row and insert in batches
    report = Report()
    handled: dict[str, int] = {}
    batch: list = []
    with _text(fileobj) as f:
        reader = csv.reader(f)
        next(reader)
        for cells in reader:
            line = reader.line_num
            report.total += 1
            try:
                clean, normalised, warnings = parse_cells(cells, idx, ncols, known_robots, now)
            except RowRejected as r:
                report.skip(line, cells[0].strip() if cells else None, r.reason, r.detail)
                continue
            eid = clean.episode_id
            if eid in conflicts:
                report.skip(line, eid, "conflicting_duplicate_in_file",
                            f"id appears on lines {valid_lines[eid]} with different values; none imported")
            elif eid in handled:
                report.skip(line, eid, "duplicate_in_file", f"identical to line {handled[eid]}")
            else:
                handled[eid] = line
                batch.append((line, clean, normalised, warnings))
                if len(batch) >= BATCH_SIZE:
                    _flush(db, batch, run, report)
                    batch = []
    _flush(db, batch, run, report)

    result = report.as_dict()
    run.finished_at = utcnow()
    run.total_rows, run.imported, run.skipped, run.report = report.total, report.imported, report.skipped, result
    db.commit()
    return {"import_run_id": run.id, "filename": run.filename, "sha256": run.sha256, **result}
