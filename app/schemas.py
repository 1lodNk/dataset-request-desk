"""Request/response models (pydantic v2). Input models are strict about what they accept."""
import re
from datetime import date, datetime
from typing import Generic, Literal, TypeVar, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .domain import QUALITIES, ROLES, STATUSES
from .security import MAX_PASSWORD_BYTES

T = TypeVar("T")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

Role = Literal["client", "operator", "admin"]
Status = Literal["submitted", "in_progress", "delivered", "accepted", "rejected"]
Quality = Literal["good", "usable", "bad"]
assert set(ROLES) == set(get_args(Role)) and set(STATUSES) == set(get_args(Status))  # keep in sync
assert set(QUALITIES) == set(get_args(Quality))


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


# ---- auth / users -----------------------------------------------------------------------------
class LoginIn(BaseModel):
    email: str = Field(max_length=255)
    password: str = Field(max_length=200)


class UserOut(ORM):
    id: int
    email: str
    name: str
    organisation: str | None
    role: Role
    is_active: bool
    created_at: datetime


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: UserOut


def _check_password(v: str) -> str:
    if len(v) < 8:
        raise ValueError("password must be at least 8 characters")
    if len(v.encode()) > MAX_PASSWORD_BYTES:
        raise ValueError(f"password must be at most {MAX_PASSWORD_BYTES} bytes")
    return v


class UserCreate(BaseModel):
    email: str = Field(max_length=255)
    name: str = Field(min_length=1, max_length=200)
    organisation: str | None = Field(default=None, max_length=200)
    role: Role
    password: str

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip().lower()
        if not _EMAIL.match(v):
            raise ValueError("invalid email address")
        return v

    @field_validator("password")
    @classmethod
    def _pw(cls, v: str) -> str:
        return _check_password(v)


class UserPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    organisation: str | None = Field(default=None, max_length=200)
    role: Role | None = None
    is_active: bool | None = None
    password: str | None = None

    @field_validator("password")
    @classmethod
    def _pw(cls, v: str | None) -> str | None:
        return None if v is None else _check_password(v)


# ---- requests ---------------------------------------------------------------------------------
class RequestCreate(BaseModel):
    task_name: str = Field(min_length=1, max_length=200)
    episodes_requested: int = Field(gt=0, le=100_000)
    deadline: date
    notes: str = Field(default="", max_length=5000)

    @field_validator("task_name")
    @classmethod
    def _task(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("task_name must not be blank")
        return v

    @field_validator("deadline")
    @classmethod
    def _future(cls, v: date) -> date:
        if v < date.today():
            raise ValueError("deadline must not be in the past")
        return v


class RequestOut(BaseModel):
    id: int
    client_id: int
    client_name: str
    client_organisation: str | None
    task_name: str
    episodes_requested: int
    assigned_count: int
    deadline: date
    notes: str
    status: Status
    created_at: datetime
    updated_at: datetime
    allowed_transitions: list[Status]  # what *this* caller may do next (UI hint; server re-checks)


class TransitionIn(BaseModel):
    to_status: Status
    comment: str | None = Field(default=None, max_length=1000)


class HistoryOut(BaseModel):
    from_status: Status | None
    to_status: Status
    changed_by_id: int
    changed_by_name: str
    changed_by_role: Role
    changed_at: datetime
    comment: str | None


# ---- episodes ---------------------------------------------------------------------------------
class EpisodeOut(BaseModel):
    episode_id: str
    robot_id: str
    task_name: str
    recorded_at: datetime
    duration_seconds: int
    operator_name: str | None
    quality: Quality
    assigned_request_id: int | None = None


class EpisodePage(BaseModel):
    items: list[EpisodeOut]
    limit: int
    offset: int
    has_more: bool


class AssignIn(BaseModel):
    episode_ids: list[str] = Field(min_length=1, max_length=500)

    @field_validator("episode_ids")
    @classmethod
    def _norm(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(i.strip().upper() for i in v if i.strip()))  # de-dupe, keep order


class AssignOut(BaseModel):
    assigned: list[str]
    already_assigned_to_this_request: list[str]
    assigned_count: int
