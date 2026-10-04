import os
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient
from app.db.base import Base
from app.main import app
from app.auth.security import hash_password
from app.models import User
from app.db.session import get_db

TEST_DB = os.getenv("DATABASE_URL", "postgresql+psycopg://dataset:dataset@localhost:5432/dataset_desk_test")
engine = create_engine(TEST_DB)
TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

@pytest.fixture(scope="session", autouse=True)
def schema():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)

@pytest.fixture()
def db():
    session = TestingSession()
    try:
        yield session
    finally:
        session.rollback()
        for table in reversed(Base.metadata.sorted_tables):
            session.execute(table.delete())
        session.commit()
        session.close()

@pytest.fixture()
def client(db):
    def override():
        yield db
    app.dependency_overrides[get_db] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()

def add_user(db, email, role, name="Test User"):
    user = User(email=email, password_hash=hash_password("password123"), role=role, name=name, is_active=True)
    db.add(user); db.commit(); db.refresh(user)
    return user

def token_for(client, email, password="password123"):
    return client.post('/auth/login', json={'email': email, 'password': password}).json()['access_token']
