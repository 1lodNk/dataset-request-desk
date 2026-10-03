"""Domain constants and the small pure helpers shared by several modules."""
import re

ROLES = ("client", "operator", "admin")
STAFF_ROLES = ("operator", "admin")
QUALITIES = ("good", "usable", "bad")
ASSIGNABLE_QUALITIES = ("good", "usable")
STATUSES = ("submitted", "in_progress", "delivered", "accepted", "rejected")

# (from, to) -> roles allowed to perform that step. This table IS the workflow.
TRANSITIONS: dict[tuple[str, str], tuple[str, ...]] = {
    ("submitted", "in_progress"): STAFF_ROLES,
    ("in_progress", "delivered"): STAFF_ROLES,
    ("delivered", "accepted"): ("client",),
    ("delivered", "rejected"): ("client",),
    ("rejected", "in_progress"): STAFF_ROLES,  # rework
}


def next_statuses(status: str) -> list[str]:
    return [to for (frm, to) in TRANSITIONS if frm == status]


def normalize_task_name(value: str) -> str:
    """'  Pick   CUP ' -> 'pick cup'. Used for episodes and requests so filters/joins line up."""
    return re.sub(r"\s+", " ", value or "").strip().lower()
