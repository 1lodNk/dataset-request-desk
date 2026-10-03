"""Test harness.

By default tests run against a throw-away SQLite file (zero setup: `pytest`).
Set TEST_DATABASE_URL=postgresql+psycopg://... to run the very same suite against PostgreSQL;
that also enables the concurrency tests, which need real row locks.
The schema is built by running the real Alembic migrations, so migrations are tested too."""
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

_tmp = tempfile.mkdtemp(prefix="desk-tests-")
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{_tmp}/test.sqlite"
os.environ["BCRYPT_ROUNDS"] = "4"  # fast hashing; production default is 12
os.environ["APP_ENV"] = "test"
os.environ["JWT_SECRET"] = "test-secret-test-secret-test-secret-123456"

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
IS_SQLITE = os.environ["DATABASE_URL"].startswith("sqlite")


@pytest.fixture(scope="session", autouse=True)
def _schema():
    from app.db import engine
    from app.models import Base

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    with engine.begin() as conn:  # start from a clean slate (matters for a reused Postgres database)
        Base.metadata.drop_all(conn)
        conn.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(autouse=True)
def _clean_tables():
    from app.db import engine
    from app.models import Base

    yield
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            if table.name != "robots":  # reference data created by the migration
                conn.execute(table.delete())


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as c:  # context manager runs the lifespan (binds the event broker)
        yield c


@pytest.fixture
def db():
    from app.db import SessionLocal

    with SessionLocal() as session:
        yield session


@pytest.fixture
def users(db):
    from app.services.users import create_user

    def mk(role, email, name, org=None):
        return create_user(db, email=email, name=name, role=role, password="password123", organisation=org)

    return SimpleNamespace(
        admin=mk("admin", "admin@t.io", "Ada Admin"),
        ops=mk("operator", "ops@t.io", "Olu Operator"),
        ops2=mk("operator", "ops2@t.io", "Odile Operator"),
        a=mk("client", "a@t.io", "Client A", "Acme"),
        b=mk("client", "b@t.io", "Client B", "Beta"),
    )


@pytest.fixture
def H():
    """H(user) -> Authorization headers for that user."""
    from app.security import create_access_token

    return lambda user: {"Authorization": f"Bearer {create_access_token(user.id)[0]}"}
