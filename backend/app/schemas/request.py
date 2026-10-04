from datetime import date, datetime
from pydantic import BaseModel, Field

class RequestCreate(BaseModel):
    task_name: str = Field(min_length=1, max_length=200)
    episodes_requested: int = Field(gt=0, le=1_000_000)
    deadline: date
    notes: str | None = Field(default=None, max_length=5000)

class StatusChange(BaseModel):
    status: str

class RequestOut(BaseModel):
    id: int
    client_id: int
    task_name: str
    episodes_requested: int
    deadline: date
    notes: str | None
    status: str
    created_at: datetime
    updated_at: datetime
    assigned_count: int = 0
    model_config = {"from_attributes": True}
