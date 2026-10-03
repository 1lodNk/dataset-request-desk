"""Operational commands:  python -m app.cli seed-users | import-episodes FILE"""
import argparse
import json
import sys
from pathlib import Path

from .config import settings
from .db import SessionLocal
from .services import importer, users


def seed_users(path: Path) -> None:
    """Create the accounts in users.json. Idempotent: existing emails are left untouched
    (so re-running never resets a password someone has changed)."""
    created = skipped = 0
    with SessionLocal() as db:
        for u in json.loads(path.read_text()):
            if users.get_by_email(db, u["email"]):
                skipped += 1
                continue
            users.create_user(db, email=u["email"], name=u["name"], role=u["role"], password=u["password"],
                              organisation=u.get("organisation"))
            created += 1
    print(f"seed-users: created {created}, already present {skipped}")


def import_episodes(path: Path, as_json: bool) -> None:
    with SessionLocal() as db, path.open("rb") as f:
        result = importer.import_episodes(db, f, filename=path.name, user_id=None)
    if as_json:
        print(json.dumps(result, indent=2, default=str))
        return
    print(f"import {path.name}: {result['total_rows']} rows -> imported {result['imported']}, "
          f"skipped {result['skipped']}, normalised {result['normalised_rows']}")
    for reason, n in result["skipped_by_reason"].items():
        print(f"  skipped {n:>4}  {reason}")
    for reason, n in result["warnings"].items():
        print(f"  warning {n:>4}  {reason}")


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("seed-users")
    p.add_argument("--file", type=Path, default=settings.seed_dir / "users.json")
    p = sub.add_parser("import-episodes")
    p.add_argument("file", type=Path)
    p.add_argument("--json", action="store_true", help="print the full report, including every skipped row")
    args = parser.parse_args()
    if args.cmd == "seed-users":
        seed_users(args.file)
    else:
        import_episodes(args.file, args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
