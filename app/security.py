"""Password hashing (bcrypt) and signed access tokens (JWT, HS256)."""
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from .config import settings
from .errors import Unauthorized

ALGORITHM = "HS256"
MAX_PASSWORD_BYTES = 72  # bcrypt's hard limit; longer inputs are rejected, never silently truncated


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=settings.bcrypt_rounds)).decode()


# Used to burn the same CPU time when the email is unknown, so response time doesn't reveal
# which emails exist.
_DUMMY_HASH = hash_password("not-a-real-password")


def verify_password(password: str, password_hash: str | None) -> bool:
    raw = password.encode()
    if len(raw) > MAX_PASSWORD_BYTES:
        return False
    try:
        return bcrypt.checkpw(raw, (password_hash or _DUMMY_HASH).encode()) and password_hash is not None
    except ValueError:  # malformed stored hash
        return False


def create_access_token(user_id: int) -> tuple[str, datetime]:
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=settings.jwt_ttl_minutes)
    token = jwt.encode(
        {"sub": str(user_id), "iat": int(now.timestamp()), "exp": int(exp.timestamp())},
        settings.jwt_secret,
        algorithm=ALGORITHM,
    )
    return token, exp


def decode_access_token(token: str) -> dict:
    try:
        # algorithms is pinned: never let the token choose how it is verified ("alg: none" attacks)
        return jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM], options={"require": ["exp", "sub"]})
    except jwt.PyJWTError:
        raise Unauthorized("invalid or expired token") from None
