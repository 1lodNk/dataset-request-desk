"""Analytics, computed entirely in the database (GROUP BY / window functions / percentile_cont).

Definitions (also in README):
  * episodes per day per robot  — episodes whose recorded_at falls in [from, to], bucketed by UTC day.
  * requests by status          — requests *created* in the range, by their current status.
  * median submit->deliver time — requests whose *first* delivery happened in the range; time measured
                                  from creation (status 'submitted') to the first transition to
                                  'delivered', so rework cycles don't change a request's number.
  * top 5 task names            — most 'good' episodes recorded in the range (ties broken by name).
"""
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import Date, cast, desc, func, select, text
from sqlalchemy.orm import Session

from ..domain import STATUSES
from ..models import Episode, Request, RequestStatusEvent

MAX_RANGE_DAYS = 400


def _bounds(start: date, end: date) -> tuple[datetime, datetime]:
    lo = datetime.combine(start, time.min, tzinfo=timezone.utc)
    hi = datetime.combine(end + timedelta(days=1), time.min, tzinfo=timezone.utc)
    return lo, hi  # half-open [lo, hi): index-friendly and no end-of-day edge cases


def _median_delivery_seconds(db: Session, lo: datetime, hi: datetime) -> tuple[float | None, int]:
    first_delivery = (
        select(RequestStatusEvent.request_id, func.min(RequestStatusEvent.changed_at).label("delivered_at"))
        .where(RequestStatusEvent.to_status == "delivered")
        .group_by(RequestStatusEvent.request_id)
        .subquery()
    )
    if db.get_bind().dialect.name == "postgresql":
        seconds = func.extract("epoch", first_delivery.c.delivered_at - Request.created_at)
        row = db.execute(
            select(func.percentile_cont(0.5).within_group(seconds), func.count())
            .select_from(Request).join(first_delivery, first_delivery.c.request_id == Request.id)
            .where(first_delivery.c.delivered_at >= lo, first_delivery.c.delivered_at < hi)
        ).one()
        return (float(row[0]) if row[0] is not None else None), row[1]

    # SQLite (dev/tests only) has no percentile_cont: median via window functions, same result.
    row = db.execute(
        text(
            """
            WITH fd AS (
              SELECT request_id, MIN(changed_at) AS delivered_at FROM request_status_history
              WHERE to_status = 'delivered' GROUP BY request_id),
            d AS (
              SELECT (julianday(fd.delivered_at) - julianday(r.created_at)) * 86400.0 AS secs
              FROM requests r JOIN fd ON fd.request_id = r.id
              WHERE fd.delivered_at >= :lo AND fd.delivered_at < :hi),
            ranked AS (SELECT secs, ROW_NUMBER() OVER (ORDER BY secs) AS rn, COUNT(*) OVER () AS cnt FROM d)
            SELECT AVG(secs), MAX(cnt) FROM ranked WHERE rn IN ((cnt + 1) / 2, (cnt + 2) / 2)
            """
        ),
        {"lo": lo.strftime("%Y-%m-%d %H:%M:%S.%f"), "hi": hi.strftime("%Y-%m-%d %H:%M:%S.%f")},
    ).one()
    return (float(row[0]) if row[0] is not None else None), int(row[1] or 0)


def compute(db: Session, start: date, end: date) -> dict:
    lo, hi = _bounds(start, end)

    day = func.date(Episode.recorded_at).label("day")  # UTC: the connection time zone is pinned to UTC
    per_day_robot = [
        {"day": str(d), "robot_id": robot, "episodes": n}
        for d, robot, n in db.execute(
            select(day, Episode.robot_id, func.count())
            .where(Episode.recorded_at >= lo, Episode.recorded_at < hi)
            .group_by(day, Episode.robot_id).order_by(day, Episode.robot_id)
        )
    ]

    counts = dict(db.execute(
        select(Request.status, func.count()).where(Request.created_at >= lo, Request.created_at < hi)
        .group_by(Request.status)
    ).all())
    by_status = {s: counts.get(s, 0) for s in STATUSES}

    median_seconds, delivered = _median_delivery_seconds(db, lo, hi)

    top_tasks = [
        {"task_name": name, "good_episodes": n}
        for name, n in db.execute(
            select(Episode.task_name, func.count().label("n"))
            .where(Episode.quality == "good", Episode.recorded_at >= lo, Episode.recorded_at < hi)
            .group_by(Episode.task_name).order_by(desc("n"), Episode.task_name).limit(5)
        )
    ]
    return {
        "from": start, "to": end,
        "episodes_per_day_per_robot": per_day_robot,
        "requests_by_status": by_status,
        "median_submit_to_deliver_seconds": median_seconds,
        "delivered_requests_in_range": delivered,
        "top_tasks_by_good_episodes": top_tasks,
    }
