"""FastAPI dependencies: authentication and role-based authorisation (all enforced server-side)."""
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .db import SessionLocal, get_db
from .domain import STAFF_ROLES
from .errors import Forbidden, Unauthorized
from .models import User
from .security import decode_access_token

bearer = HTTPBearer(auto_error=False)


def user_from_token(db: Session, token: str) -> tuple[User, int]:
    payload = decode_access_token(token)
    try:
        user = db.get(User, int(payload["sub"]))
    except (ValueError, TypeError):
        raise Unauthorized("invalid or expired token") from None
    # Looked up on every request: deactivating a user or changing a role takes effect immediately,
    # instead of waiting for a long-lived token to expire.
    if user is None or not user.is_active:
        raise Unauthorized("invalid or expired token")
    return user, int(payload["exp"])


def current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if creds is None:
        raise Unauthorized("authentication required")
    user, _ = user_from_token(db, creds.credentials)
    request.state.user_id = user.id  # picked up by the access-log middleware
    return user


def stream_user(
    request: Request, creds: HTTPAuthorizationCredentials | None = Depends(bearer)
) -> tuple[User, int]:
    """Same auth, but with a short-lived private session: an SSE connection must not hold a pooled
    DB connection open for as long as the stream lives."""
    if creds is None:
        raise Unauthorized("authentication required")
    with SessionLocal() as db:
        user, exp = user_from_token(db, creds.credentials)
    request.state.user_id = user.id
    return user, exp


def require_roles(*roles: str):
    def checker(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise Forbidden(f"requires role: {' or '.join(roles)}")
        return user

    return checker


require_staff = require_roles(*STAFF_ROLES)
require_admin = require_roles("admin")
require_client = require_roles("client")
