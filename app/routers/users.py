from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_admin
from ..models import User
from ..schemas import UserCreate, UserOut, UserPatch
from ..services import users

router = APIRouter(prefix="/api/users", tags=["users (admin)"])


@router.get("", response_model=list[UserOut])
def list_users(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    return users.list_users(db)


@router.post("", response_model=UserOut, status_code=201)
def create_user(body: UserCreate, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    return users.create_user(db, email=body.email, name=body.name, role=body.role,
                             password=body.password, organisation=body.organisation)


@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserPatch, actor: User = Depends(require_admin),
                db: Session = Depends(get_db)):
    return users.update_user(db, actor, user_id, body.model_dump(exclude_unset=True))
