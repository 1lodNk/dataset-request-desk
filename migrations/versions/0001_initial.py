"""initial schema

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

BigPK = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("organisation", sa.String(200)),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", TS, nullable=False),
        sa.CheckConstraint("role IN ('client','operator','admin')", name="ck_users_role"),
    )

    op.create_table("robots", sa.Column("robot_id", sa.String(64), primary_key=True))
    robots = sa.table("robots", sa.column("robot_id", sa.String))
    op.bulk_insert(
        robots,
        [{"robot_id": r} for r in ("arm-01", "arm-02", "arm-03", "mobile-01", "humanoid-01")],
    )

    op.create_table(
        "import_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("started_by", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("started_at", TS, nullable=False),
        sa.Column("finished_at", TS),
        sa.Column("total_rows", sa.Integer(), nullable=False),
        sa.Column("imported", sa.Integer(), nullable=False),
        sa.Column("skipped", sa.Integer(), nullable=False),
        sa.Column("report", sa.JSON()),
    )

    op.create_table(
        "episodes",
        sa.Column("id", BigPK, primary_key=True),
        sa.Column("episode_id", sa.String(64), nullable=False, unique=True),
        sa.Column("robot_id", sa.String(64), sa.ForeignKey("robots.robot_id"), nullable=False),
        sa.Column("task_name", sa.String(200), nullable=False),
        sa.Column("recorded_at", TS, nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("operator_name", sa.String(200)),
        sa.Column("quality", sa.String(16), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("import_run_id", sa.Integer(), sa.ForeignKey("import_runs.id")),
        sa.CheckConstraint("quality IN ('good','usable','bad')", name="ck_episodes_quality"),
        sa.CheckConstraint("duration_seconds > 0", name="ck_episodes_duration"),
    )
    op.create_index("ix_episodes_recorded_robot", "episodes", ["recorded_at", "robot_id"])
    op.create_index("ix_episodes_task_quality_recorded", "episodes", ["task_name", "quality", "recorded_at"])
    op.create_index(
        "ix_episodes_good_task_recorded", "episodes", ["task_name", "recorded_at"],
        postgresql_where=sa.text("quality = 'good'"), sqlite_where=sa.text("quality = 'good'"),
    )

    op.create_table(
        "requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("client_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("task_name", sa.String(200), nullable=False),
        sa.Column("episodes_requested", sa.Integer(), nullable=False),
        sa.Column("deadline", sa.Date(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.CheckConstraint(
            "status IN ('submitted','in_progress','delivered','accepted','rejected')",
            name="ck_requests_status",
        ),
        sa.CheckConstraint("episodes_requested > 0", name="ck_requests_count"),
    )
    op.create_index("ix_requests_client_created", "requests", ["client_id", "created_at"])
    op.create_index("ix_requests_status_created", "requests", ["status", "created_at"])
    op.create_index("ix_requests_created", "requests", ["created_at"])

    op.create_table(
        "request_status_history",
        sa.Column("id", BigPK, primary_key=True),
        sa.Column("request_id", sa.Integer(), sa.ForeignKey("requests.id"), nullable=False),
        sa.Column("from_status", sa.String(16)),
        sa.Column("to_status", sa.String(16), nullable=False),
        sa.Column("changed_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("changed_at", TS, nullable=False),
        sa.Column("comment", sa.Text()),
    )
    op.create_index("ix_history_request_changed", "request_status_history", ["request_id", "changed_at"])
    op.create_index(
        "ix_history_delivered", "request_status_history", ["request_id", "changed_at"],
        postgresql_where=sa.text("to_status = 'delivered'"), sqlite_where=sa.text("to_status = 'delivered'"),
    )

    op.create_table(
        "assignments",
        sa.Column("id", BigPK, primary_key=True),
        sa.Column("request_id", sa.Integer(), sa.ForeignKey("requests.id"), nullable=False),
        sa.Column("episode_id", BigPK, sa.ForeignKey("episodes.id"), nullable=False),
        sa.Column("assigned_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("assigned_at", TS, nullable=False),
        sa.UniqueConstraint("episode_id", name="uq_assignments_episode"),
    )
    op.create_index("ix_assignments_request", "assignments", ["request_id"])


def downgrade() -> None:
    for table in (
        "assignments", "request_status_history", "requests", "episodes", "import_runs", "robots", "users",
    ):
        op.drop_table(table)
