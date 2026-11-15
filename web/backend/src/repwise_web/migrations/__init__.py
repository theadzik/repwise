"""Schema changes, applied before the API starts.

Every pod runs this as it starts, and two can start together. A lock held in
the database makes the second wait for the first, after which it finds nothing
left to do.
"""

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

#: Any number no other user of the database would pick for an advisory lock.
_LOCK = 7_265_707_769_736_501


def upgrade(database_url: str) -> None:
    engine = create_engine(database_url)
    with engine.connect() as connection:
        connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": _LOCK})
        try:
            config = Config()
            config.set_main_option("script_location", "repwise_web:migrations")
            # Handed to env.py, so that the migration runs on the connection
            # that holds the lock.
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            connection.commit()
        finally:
            connection.rollback()
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _LOCK})
            connection.commit()
    engine.dispose()
