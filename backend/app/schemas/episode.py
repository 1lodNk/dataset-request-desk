from datetime import datetime
from pydantic import BaseModel

class EpisodeOut(BaseModel):
    id: int
    episode_id: str
    robot_id: str
    task_name: str
    recorded_at: datetime
    duration_seconds: int
    operator_name: str | None
    quality: str
    assigned_request_id: int | None = None
    model_config = {"from_attributes": True}

class ImportResult(BaseModel):
    imported: int
    skipped: int
    duplicates: int
    invalid: int
    errors: list[dict]
