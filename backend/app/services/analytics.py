from datetime import date, datetime, time, timezone
from sqlalchemy import text
from sqlalchemy.orm import Session

def analytics(db: Session, start_date: date, end_date: date):
    start = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
    end = datetime.combine(end_date, time.min, tzinfo=timezone.utc)
    rows = db.execute(text("""
        SELECT recorded_at::date AS day, robot_id, COUNT(*)::int AS count
        FROM episodes
        WHERE recorded_at >= :start AND recorded_at < :end
        GROUP BY recorded_at::date, robot_id
        ORDER BY day, robot_id
    """), {"start": start, "end": end}).mappings().all()
    statuses = db.execute(text("""
        SELECT status, COUNT(*)::int AS count
        FROM requests
        WHERE created_at >= :start AND created_at < :end
        GROUP BY status ORDER BY status
    """), {"start": start, "end": end}).mappings().all()
    median = db.execute(text("""
        SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (delivered.changed_at - submitted.changed_at))) AS median_seconds
        FROM requests r
        JOIN request_status_history submitted ON submitted.request_id = r.id AND submitted.to_status = 'submitted'
        JOIN request_status_history delivered ON delivered.request_id = r.id AND delivered.to_status = 'delivered' AND delivered.changed_at >= submitted.changed_at
        WHERE r.created_at >= :start AND r.created_at < :end
    """), {"start": start, "end": end}).scalar()
    tasks = db.execute(text("""
        SELECT task_name, COUNT(*)::int AS count
        FROM episodes
        WHERE quality = 'good' AND recorded_at >= :start AND recorded_at < :end
        GROUP BY task_name ORDER BY count DESC, task_name LIMIT 5
    """), {"start": start, "end": end}).mappings().all()
    return {
        "start_date": start_date, "end_date": end_date,
        "episodes_per_day_robot": [dict(x) for x in rows],
        "requests_by_status": [dict(x) for x in statuses],
        "median_submitted_to_delivered_seconds": float(median) if median is not None else None,
        "top_good_tasks": [dict(x) for x in tasks],
    }
