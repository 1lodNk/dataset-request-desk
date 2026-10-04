from datetime import datetime
from sqlalchemy import DateTime, Integer, String, CheckConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base

class Episode(Base):
    __tablename__ = "episodes"
    __table_args__ = (
        CheckConstraint("quality IN ('good','usable','bad')", name="ck_episode_quality"),
        CheckConstraint("duration_seconds > 0", name="ck_episode_duration_positive"),
        Index("ix_episodes_recorded_robot", "recorded_at", "robot_id"),
        Index("ix_episodes_quality_recorded", "quality", "recorded_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    robot_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    task_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    operator_name: Mapped[str | None] = mapped_column(String(200))
    quality: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    assignments = relationship("Assignment", back_populates="episode")
