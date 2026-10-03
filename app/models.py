"""SQLAlchemy models. Constraints live in the database, not just in Python:
the DB is the last line of defence against races (e.g. UNIQUE(assignments.episode_id))."""
from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON, BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer,
    String, Text, UniqueConstraint, text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """timestamptz on PostgreSQL; on SQLite (tests) always read back as aware-UTC.
    Refuses naive datetimes so we never silently mix local and UTC times."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime not allowed; use timezone-aware UTC")
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


# BIGINT PKs on PostgreSQL (episodes will be in the millions); INTEGER on SQLite so it autoincrements.
BigPK = BigInteger().with_variant(Integer, "sqlite")


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('client','operator','admin')", name="ck_users_role"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)  # stored lower-cased
    name: Mapped[str] = mapped_column(String(200))
    organisation: Mapped[str | None] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(16))
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Robot(Base):
    __tablename__ = "robots"
    robot_id: Mapped[str] = mapped_column(String(64), primary_key=True)


class ImportRun(Base):
    __tablename__ = "import_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    started_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))  # NULL = CLI/system
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    imported: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    report: Mapped[dict | None] = mapped_column(JSON)


class Episode(Base):
    __tablename__ = "episodes"
    __table_args__ = (
        CheckConstraint("quality IN ('good','usable','bad')", name="ck_episodes_quality"),
        CheckConstraint("duration_seconds > 0", name="ck_episodes_duration"),
        # analytics: per-day/per-robot over a date range
        Index("ix_episodes_recorded_robot", "recorded_at", "robot_id"),
        # operator picker: filter by task + quality, newest first
        Index("ix_episodes_task_quality_recorded", "task_name", "quality", "recorded_at"),
        # analytics: top tasks by *good* episodes — partial index stays small and hot
        Index(
            "ix_episodes_good_task_recorded", "task_name", "recorded_at",
            postgresql_where=text("quality = 'good'"), sqlite_where=text("quality = 'good'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigPK, primary_key=True)
    episode_id: Mapped[str] = mapped_column(String(64), unique=True)  # business key from the export
    robot_id: Mapped[str] = mapped_column(ForeignKey("robots.robot_id"))
    task_name: Mapped[str] = mapped_column(String(200))
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime)
    duration_seconds: Mapped[int] = mapped_column(Integer)
    operator_name: Mapped[str | None] = mapped_column(String(200))
    quality: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    import_run_id: Mapped[int | None] = mapped_column(ForeignKey("import_runs.id"))  # provenance


class Request(Base):
    __tablename__ = "requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('submitted','in_progress','delivered','accepted','rejected')",
            name="ck_requests_status",
        ),
        CheckConstraint("episodes_requested > 0", name="ck_requests_count"),
        Index("ix_requests_client_created", "client_id", "created_at"),
        Index("ix_requests_status_created", "status", "created_at"),
        Index("ix_requests_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    task_name: Mapped[str] = mapped_column(String(200))
    episodes_requested: Mapped[int] = mapped_column(Integer)
    deadline: Mapped[date] = mapped_column(Date)
    notes: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    status: Mapped[str] = mapped_column(String(16), default="submitted")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class RequestStatusEvent(Base):
    """Append-only audit log: who moved which request from where to where, and when."""

    __tablename__ = "request_status_history"
    __table_args__ = (
        Index("ix_history_request_changed", "request_id", "changed_at"),
        # analytics: first delivery time per request
        Index(
            "ix_history_delivered", "request_id", "changed_at",
            postgresql_where=text("to_status = 'delivered'"), sqlite_where=text("to_status = 'delivered'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigPK, primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id"))
    from_status: Mapped[str | None] = mapped_column(String(16))  # NULL on creation
    to_status: Mapped[str] = mapped_column(String(16))
    changed_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    changed_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    comment: Mapped[str | None] = mapped_column(Text)


class Assignment(Base):
    """An episode assigned to a request. UNIQUE(episode_id) is the 'at most one request at a time'
    rule enforced by the database, so it holds even under concurrent writers."""

    __tablename__ = "assignments"
    __table_args__ = (
        UniqueConstraint("episode_id", name="uq_assignments_episode"),
        Index("ix_assignments_request", "request_id"),
    )

    id: Mapped[int] = mapped_column(BigPK, primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id"))
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id"))
    assigned_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    assigned_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
