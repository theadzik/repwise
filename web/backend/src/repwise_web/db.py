"""The tables, and how a process reaches them.

Four tables hold everything the service knows. Each row belongs to an athlete,
so a second athlete is more rows and not a different schema.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    create_engine,
    func,
    text,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    sessionmaker,
)

QUEUED, RUNNING, SUCCEEDED, FAILED = "queued", "running", "succeeded", "failed"

Sessions = sessionmaker[Session]


class Base(DeclarativeBase):
    type_annotation_map = {datetime: DateTime(timezone=True)}  # noqa: RUF012


class Athlete(Base):
    __tablename__ = "athlete"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    #: What oauth2-proxy says the signed-in account is. A login can be renamed,
    #: which is as far as the allow-list in front of this can be trusted too.
    github_login: Mapped[str] = mapped_column(unique=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Run(Base):
    """One press of the button, and the queue entry for it.

    There is one kind of job and it needs a table for its history anyway, so
    the table is the queue: a worker claims a queued row and reports back on it.
    """

    __tablename__ = "run"
    __table_args__ = (
        CheckConstraint(
            f"status IN ('{QUEUED}', '{RUNNING}', '{SUCCEEDED}', '{FAILED}')",
            name="run_status",
        ),
        # The lock that stops two runs overlapping, held by the database so
        # that it holds across replicas: two presses at once insert one row.
        Index(
            "run_one_active",
            "athlete_id",
            unique=True,
            postgresql_where=text(f"status IN ('{QUEUED}', '{RUNNING}')"),
        ),
        Index("run_newest_first", "athlete_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    athlete_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("athlete.id", ondelete="CASCADE")
    )
    status: Mapped[str] = mapped_column(default=QUEUED)
    exit_code: Mapped[int | None]
    output: Mapped[str] = mapped_column(default="")
    #: Why a run failed, when it was not repwise that said so.
    error: Mapped[str | None]
    claimed_by: Mapped[str | None]
    heartbeat_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]


class GarminConnection(Base):
    __tablename__ = "garmin_connection"

    athlete_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("athlete.id", ondelete="CASCADE"), primary_key=True
    )
    token_ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    #: Raised on every write. A run writes back only against the version it
    #: read, so a login made meanwhile is not overwritten with an older token.
    version: Mapped[int] = mapped_column(default=1)
    connected_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Dump(Base):
    """One file of repwise's session cache, its `dump_dir`, kept between runs.

    A run's home is deleted when it ends, and the cache with it, so without
    these every run downloads every session in the search window again.
    """

    __tablename__ = "dump"

    athlete_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("athlete.id", ondelete="CASCADE"), primary_key=True
    )
    #: The file's name in `dump_dir`, which holds no directories.
    name: Mapped[str] = mapped_column(primary_key=True)
    content: Mapped[bytes] = mapped_column(LargeBinary)


def connect(database_url: str) -> Sessions:
    """A session factory for one process. Nothing is opened until it is used."""
    engine = create_engine(database_url, pool_pre_ping=True)
    return sessionmaker(engine, expire_on_commit=False)
