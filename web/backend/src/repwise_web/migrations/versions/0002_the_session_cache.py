"""The session cache.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dump",
        sa.Column(
            "athlete_id",
            sa.Uuid,
            sa.ForeignKey("athlete.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("name", sa.String, primary_key=True),
        sa.Column("content", sa.LargeBinary, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("dump")
