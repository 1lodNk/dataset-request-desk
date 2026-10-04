"""initial schema"""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("users",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False), sa.Column("name", sa.String(200), nullable=False),
        sa.Column("role", sa.String(20), nullable=False), sa.Column("organisation", sa.String(200)),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True); op.create_index("ix_users_role", "users", ["role"])
    op.create_table("requests",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("client_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("task_name", sa.String(200), nullable=False), sa.Column("episodes_requested", sa.Integer(), nullable=False), sa.Column("deadline", sa.Date(), nullable=False),
        sa.Column("notes", sa.Text()), sa.Column("status", sa.String(20), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("episodes_requested > 0", name="ck_requests_positive_count"),
    )
    for name, col in [("ix_requests_client_id","client_id"),("ix_requests_task_name","task_name"),("ix_requests_status","status"),("ix_requests_created_at","created_at")]: op.create_index(name,"requests",[col])
    op.create_table("episodes",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("episode_id", sa.String(100), nullable=False), sa.Column("robot_id", sa.String(100), nullable=False),
        sa.Column("task_name", sa.String(200), nullable=False), sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False), sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("operator_name", sa.String(200)), sa.Column("quality", sa.String(20), nullable=False),
        sa.CheckConstraint("quality IN ('good','usable','bad')", name="ck_episode_quality"), sa.CheckConstraint("duration_seconds > 0", name="ck_episode_duration_positive"),
    )
    op.create_index("ix_episodes_episode_id","episodes",["episode_id"],unique=True); op.create_index("ix_episodes_robot_id","episodes",["robot_id"]); op.create_index("ix_episodes_task_name","episodes",["task_name"]); op.create_index("ix_episodes_recorded_at","episodes",["recorded_at"]); op.create_index("ix_episodes_quality","episodes",["quality"]); op.create_index("ix_episodes_recorded_robot","episodes",["recorded_at","robot_id"]); op.create_index("ix_episodes_quality_recorded","episodes",["quality","recorded_at"])
    op.create_table("assignments",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("request_id", sa.Integer(), sa.ForeignKey("requests.id", ondelete="CASCADE"), nullable=False), sa.Column("episode_id", sa.Integer(), sa.ForeignKey("episodes.id", ondelete="RESTRICT"), nullable=False), sa.Column("assigned_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("episode_id", name="uq_assignment_episode"),
    )
    op.create_index("ix_assignments_request_id","assignments",["request_id"])
    op.create_table("request_status_history",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("request_id", sa.Integer(), sa.ForeignKey("requests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_status", sa.String(20)), sa.Column("to_status", sa.String(20), nullable=False), sa.Column("changed_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False), sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_request_status_history_request_id","request_status_history",["request_id"]); op.create_index("ix_request_status_history_changed_at","request_status_history",["changed_at"])

def downgrade():
    op.drop_table("request_status_history"); op.drop_table("assignments"); op.drop_table("episodes"); op.drop_table("requests"); op.drop_table("users")
