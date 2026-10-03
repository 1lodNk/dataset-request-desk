"""Runtime configuration, read once from environment variables (12-factor style)."""
import os
from dataclasses import dataclass
from pathlib import Path

DEV_SECRET = "dev-only-insecure-secret-change-me"
BASE_DIR = Path(__file__).resolve().parent.parent


def _normalise_db_url(url: str) -> str:
    # Hosting providers hand out postgres:// or postgresql://; we use the psycopg3 driver.
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


@dataclass(frozen=True)
class Settings:
    database_url: str
    jwt_secret: str
    jwt_ttl_minutes: int
    env: str
    log_level: str
    bcrypt_rounds: int
    seed_dir: Path

    @property
    def is_production(self) -> bool:
        return self.env == "production"


def load_settings() -> Settings:
    s = Settings(
        database_url=_normalise_db_url(
            os.environ.get("DATABASE_URL", "postgresql+psycopg://desk:desk@localhost:5432/desk")
        ),
        jwt_secret=os.environ.get("JWT_SECRET", DEV_SECRET),
        jwt_ttl_minutes=int(os.environ.get("JWT_TTL_MINUTES", "60")),
        env=os.environ.get("APP_ENV", "development").lower(),
        log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        bcrypt_rounds=int(os.environ.get("BCRYPT_ROUNDS", "12")),
        seed_dir=Path(os.environ.get("SEED_DIR", BASE_DIR / "seed")),
    )
    if s.is_production and (s.jwt_secret == DEV_SECRET or len(s.jwt_secret) < 32):
        # Fail fast: a forgeable token signing key is worse than a service that won't start.
        raise RuntimeError("JWT_SECRET must be set to a random string of >=32 chars in production")
    return s


settings = load_settings()
