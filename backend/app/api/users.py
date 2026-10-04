from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.auth.dependencies import require_roles
from app.auth.security import hash_password
from app.db.session import get_db
from app.models import User
from app.schemas.auth import UserOut

router = APIRouter(prefix="/users", tags=["users"])

class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    name: str = Field(min_length=1, max_length=200)
    role: str
    organisation: str | None = None

class UserPatch(BaseModel):
    role: str | None = None
    is_active: bool | None = None

@router.get("", response_model=list[UserOut])
def users(db: Session = Depends(get_db), _: User = Depends(require_roles("admin"))):
    return db.scalars(select(User).order_by(User.id)).all()

@router.post("", response_model=UserOut, status_code=201)
def create_user(payload: UserCreate, db: Session = Depends(get_db), _: User = Depends(require_roles("admin"))):
    if payload.role not in {"client", "operator", "admin"}: raise HTTPException(400, "Invalid role")
    if db.scalar(select(User).where(User.email == payload.email.lower())): raise HTTPException(409, "Email already exists")
    user = User(email=payload.email.lower(), password_hash=hash_password(payload.password), name=payload.name.strip(), role=payload.role, organisation=payload.organisation)
    db.add(user); db.commit(); db.refresh(user)
    return user

@router.patch("/{user_id}", response_model=UserOut)
def patch_user(user_id: int, payload: UserPatch, db: Session = Depends(get_db), _: User = Depends(require_roles("admin"))):
    user = db.get(User, user_id)
    if not user: raise HTTPException(404, "User not found")
    if payload.role is not None:
        if payload.role not in {"client", "operator", "admin"}: raise HTTPException(400, "Invalid role")
        user.role = payload.role
    if payload.is_active is not None: user.is_active = payload.is_active
    db.commit(); db.refresh(user)
    return user
