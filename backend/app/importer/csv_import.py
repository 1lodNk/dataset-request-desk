import csv
from datetime import datetime
from io import TextIOWrapper
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.models import Episode

KNOWN_ROBOTS = {"arm-01", "arm-02", "arm-03", "mobile-01", "humanoid-01"}
VALID_QUALITY = {"good", "usable", "bad"}
DATE_FORMATS = ("%Y-%m-%dT%H:%M:%S", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S")

def normalize(value: str | None) -> str:
    return (value or "").strip()

def parse_datetime(value: str):
    value = normalize(value)
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    return None

def import_csv(db: Session, fileobj):
    reader = csv.DictReader(TextIOWrapper(fileobj, encoding="utf-8", newline=""))
    imported = skipped = duplicates = invalid = 0
    errors = []
    seen = set()
    for row_number, row in enumerate(reader, start=2):
        if not row or len(row) < 7 or None in row:
            invalid += 1; skipped += 1
            errors.append({"row": row_number, "reason": "malformed row"})
            continue
        episode_id = normalize(row.get("episode_id")).upper()
        robot_id = normalize(row.get("robot_id")).lower()
        task_name = " ".join(normalize(row.get("task_name")).lower().split())
        recorded_at = parse_datetime(row.get("recorded_at"))
        duration_raw = normalize(row.get("duration_seconds"))
        operator_name = normalize(row.get("operator_name")) or None
        quality = normalize(row.get("quality")).lower()
        reasons = []
        if not episode_id: reasons.append("missing episode_id")
        if not robot_id: reasons.append("missing robot_id")
        elif robot_id not in KNOWN_ROBOTS: reasons.append("unknown robot_id")
        if not task_name: reasons.append("missing task_name")
        if not recorded_at: reasons.append("invalid recorded_at")
        if recorded_at and recorded_at > datetime.now(): reasons.append("recorded_at is in the future")
        try: duration = int(duration_raw)
        except ValueError: duration = None
        if duration is None: reasons.append("invalid duration_seconds")
        elif duration <= 0: reasons.append("duration_seconds must be positive")
        if quality not in VALID_QUALITY: reasons.append("invalid quality")
        if reasons:
            invalid += 1; skipped += 1
            errors.append({"row": row_number, "episode_id": episode_id or None, "reason": "; ".join(reasons)})
            continue
        if episode_id in seen:
            duplicates += 1; skipped += 1
            errors.append({"row": row_number, "episode_id": episode_id, "reason": "duplicate within file"})
            continue
        seen.add(episode_id)
        exists = db.scalar(select(Episode.id).where(Episode.episode_id == episode_id))
        if exists:
            duplicates += 1; skipped += 1
            errors.append({"row": row_number, "episode_id": episode_id, "reason": "already imported"})
            continue
        db.add(Episode(episode_id=episode_id, robot_id=robot_id, task_name=task_name, recorded_at=recorded_at, duration_seconds=duration, operator_name=operator_name, quality=quality))
        try:
            with db.begin_nested():
                db.flush()
            imported += 1
        except IntegrityError:
            duplicates += 1; skipped += 1
            errors.append({"row": row_number, "episode_id": episode_id, "reason": "duplicate database record"})
    db.commit()
    return {"imported": imported, "skipped": skipped, "duplicates": duplicates, "invalid": invalid, "errors": errors}
