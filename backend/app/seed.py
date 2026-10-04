import json
from pathlib import Path
from sqlalchemy import select
from app.auth.security import hash_password
from app.db.session import SessionLocal
from app.models import User

USERS = Path("/seed/users.json")

def main():
    if not USERS.exists():
        return
    db = SessionLocal()
    try:
        for item in json.loads(USERS.read_text()):
            user = db.scalar(select(User).where(User.email == item["email"].lower()))
            if user:
                continue
            db.add(User(email=item["email"].lower(), password_hash=hash_password(item["password"]), role=item["role"], name=item["name"], organisation=item.get("organisation")))
        db.commit()
    finally:
        db.close()

if __name__ == "__main__":
    main()
