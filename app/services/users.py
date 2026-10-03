from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..errors import Conflict, Forbidden, NotFound, Unauthorized
from ..models import User
from ..security import hash_password, verify_password


def authenticate(db: Session, email: str, password: str) -> User:
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    ok = verify_password(password, user.password_hash if user else None)  # constant-ish time
    if not (user and ok and user.is_active):
        # One message for every failure mode: don't reveal whether the email exists or is disabled.
        raise Unauthorized("invalid email or password")
    return user


def create_user(
    db: Session, *, email: str, name: str, role: str, password: str, organisation: str | None = None
) -> User:
    email = email.strip().lower()
    if db.scalar(select(User.id).where(User.email == email)):
        raise Conflict("a user with this email already exists", code="email_taken")
    user = User(
        email=email, name=name, role=role, organisation=organisation, password_hash=hash_password(password)
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:  # lost a race with a concurrent create
        db.rollback()
        raise Conflict("a user with this email already exists", code="email_taken") from None
    return user


def list_users(db: Session) -> list[User]:
    return list(db.scalars(select(User).order_by(User.id)))


def update_user(db: Session, actor: User, user_id: int, changes: dict) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise NotFound("user not found")
    if user.id == actor.id and (
        changes.get("is_active") is False or ("role" in changes and changes["role"] != user.role)
    ):
        # Prevents the last admin locking everyone out of user management.
        raise Forbidden("you cannot deactivate yourself or change your own role", code="self_modification")
    for field in ("name", "organisation", "role", "is_active"):
        if field in changes and changes[field] is not None:
            setattr(user, field, changes[field])
    if changes.get("password"):
        user.password_hash = hash_password(changes["password"])
    db.commit()
    return user


def get_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email.strip().lower()))
