"""Engine / session factory. One sync SQLAlchemy engine; FastAPI runs sync endpoints in a threadpool."""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from .config import settings


def make_engine(url: str):
    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _):  # SQLite ignores FKs unless asked
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        return engine
    # Force the session time zone so date bucketing (per-day analytics) is always UTC.
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=10,
        connect_args={"options": "-c timezone=UTC"},
    )


engine = make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def get_db():
    """Request-scoped session. Services own the transaction boundary (they call commit)."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()  # rolls back anything uncommitted
