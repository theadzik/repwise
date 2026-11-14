"""What every test starts from: an empty database and a signed-in browser.

The database is a real PostgreSQL, because what is under test is mostly what
PostgreSQL does: a unique index that allows one active run, and a claim that
skips rows another worker holds. `docker compose -f web/compose.yaml up -d db`
starts the one these expect.

No test reaches Garmin or runs repwise. A stand-in command and a stand-in
`garminconnect.Garmin` take their places.
"""

import os
import stat
import sys
import textwrap
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from repwise_web import garmin, migrations
from repwise_web.api import create_app
from repwise_web.db import Athlete, Sessions, connect
from repwise_web.settings import Settings

DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://repwise:repwise@127.0.0.1:54329/repwise",
)
KEY = Fernet.generate_key().decode()
#: What garminconnect's `dumps()` produces, in shape.
TOKEN = '{"di_token": "t", "di_refresh_token": "r", "di_client_id": "c"}'
#: What oauth2-proxy adds, and what the page sends with every request.
SIGNED_IN = {"X-Forwarded-User": "Tester", "Content-Type": "application/json"}

CONFIG = """\
settings:
  garmin:
    token_store: ~/.config/repwise
workouts:
  - key: push
    garmin_workout_id: 111
  - key: pull
    garmin_workout_id: 222
"""


@pytest.fixture(scope="session")
def sessions() -> Sessions:
    migrations.upgrade(DATABASE_URL)
    return connect(DATABASE_URL)


@pytest.fixture(autouse=True)
def empty_database(sessions: Sessions) -> None:
    with sessions() as db, db.begin():
        db.execute(text("TRUNCATE athlete CASCADE"))


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG)
    return path


@pytest.fixture
def settings(tmp_path: Path, config_file: Path) -> Settings:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return Settings(
        database_url=DATABASE_URL,
        token_keys=(KEY,),
        config_url=config_file.as_uri(),
        github_token="not-a-real-token",
        scratch_dir=str(scratch),
        command=(sys.executable, "-c", "print('stand-in repwise')"),
    )


@pytest.fixture
def client(settings: Settings, sessions: Sessions) -> TestClient:
    return TestClient(create_app(settings, sessions), headers=SIGNED_IN)


@pytest.fixture
def athlete(client: TestClient) -> uuid.UUID:
    """A registered athlete, as the `client` fixture signs in."""
    return uuid.UUID(client.put("/api/v1/me").json()["id"])


@pytest.fixture
def garmin_session(sessions: Sessions, athlete: uuid.UUID) -> uuid.UUID:
    """The athlete, signed in to Garmin."""
    with sessions() as db, db.begin():
        garmin.store_login(db, garmin.TokenBox((KEY,)), athlete, TOKEN)
    return athlete


@pytest.fixture
def stand_in(tmp_path: Path) -> Callable[[str], tuple[str, ...]]:
    """Write a stand-in for the `repwise` command, and return how to run it."""

    def write(body: str) -> tuple[str, ...]:
        script = tmp_path / f"stand-in-{uuid.uuid4().hex}.py"
        script.write_text(textwrap.dedent(body))
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        return (sys.executable, str(script))

    return write


def athlete_id(sessions: Sessions, login: str) -> uuid.UUID:
    with sessions() as db:
        found = db.scalar(select(Athlete.id).where(Athlete.github_login == login))
    assert found is not None
    return found
