from datetime import date
from pydantic import BaseModel

class EpisodesPerRobotDay(BaseModel):
    day: date
    robot_id: str
    count: int

class StatusCount(BaseModel):
    status: str
    count: int

class GoodTaskCount(BaseModel):
    task_name: str
    count: int

class AnalyticsOut(BaseModel):
    start_date: date
    end_date: date
    episodes_per_day_robot: list[EpisodesPerRobotDay]
    requests_by_status: list[StatusCount]
    median_submitted_to_delivered_seconds: float | None
    top_good_tasks: list[GoodTaskCount]
