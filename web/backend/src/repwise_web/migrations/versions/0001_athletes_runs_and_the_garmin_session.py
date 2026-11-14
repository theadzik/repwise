"""Athletes, runs, and the Garmin session.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

_ACTIVE = "status IN ('queued', 'running')"


def upgrade() -> None:
    op.create_table(
        "athlete",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("github_login", sa.String, nullable=False, unique=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_table(
        "run",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column(
            "athlete_id",
            sa.Uuid,
            sa.ForeignKey("athlete.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("exit_code", sa.Integer),
        sa.Column("output", sa.String, nullable=False),
        sa.Column("error", sa.String),
        sa.Column("claimed_by", sa.String),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="run_status",
        ),
    )
    # One queued or running run per athlete: the lock that stops two runs
    # overlapping, held where every replica can see it.
    op.create_index(
        "run_one_active",
        "run",
        ["athlete_id"],
        unique=True,
        postgresql_where=sa.text(_ACTIVE),
    )
    op.create_index("run_newest_first", "run", ["athlete_id", "created_at"])
    op.create_table(
        "garmin_connection",
        sa.Column(
            "athlete_id",
            sa.Uuid,
            sa.ForeignKey("athlete.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("token_ciphertext", sa.LargeBinary, nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column(
            "connected_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("garmin_connection")
    op.drop_table("run")
    op.drop_table("athlete")
