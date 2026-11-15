"""Signing in to Garmin: the one conversation that is not REST.

Against a stand-in for `garminconnect.Garmin`. A real login is the request
Garmin rate-limits hardest, and a test must never be able to lock an account.
"""

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect

from repwise_web import garmin
from repwise_web.db import GarminConnection, Sessions

from .conftest import KEY, TOKEN

LOGIN = "/api/v1/garmin/connection/login"
#: What the browser says about the page that opened the socket.
OUR_PAGE = {"Origin": "http://testserver"}
NEW_TOKEN = '{"di_token": "new", "di_refresh_token": "new", "di_client_id": "c"}'


class FakeGarmin:
    """Signs in anyone whose password is "right", and wants a code from "mfa@"."""

    made: list[FakeGarmin] = []  # noqa: RUF012 - reset by the fixture below

    def __init__(self, email: str, password: str, *, return_on_mfa: bool) -> None:
        assert return_on_mfa
        self.email = email
        self.password: str | None = password
        self.client = SimpleNamespace(dumps=lambda: NEW_TOKEN)
        FakeGarmin.made.append(self)

    def login(self) -> tuple[str | None, None]:
        if self.password != "right":
            raise RuntimeError("Wrong password")
        return ("needs_mfa" if self.email.startswith("mfa@") else None), None

    def resume_login(self, _state: object, code: str) -> None:
        if code != "123456":
            raise RuntimeError("Wrong code")


@pytest.fixture(autouse=True)
def fake_garmin(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeGarmin.made = []
    monkeypatch.setattr(garmin.garminconnect, "Garmin", FakeGarmin)


def held(sessions: Sessions, athlete: uuid.UUID) -> GarminConnection | None:
    with sessions() as db:
        return db.scalar(
            select(GarminConnection).where(GarminConnection.athlete_id == athlete)
        )


def credentials(email: str = "me@example.com", password: str = "right") -> Any:  # noqa: ANN401 - a JSON message
    return {"email": email, "password": password}


def test_a_login_that_needs_no_code(
    client: TestClient, sessions: Sessions, athlete: uuid.UUID
) -> None:
    with client.websocket_connect(LOGIN, headers=OUR_PAGE) as socket:
        socket.send_json(credentials())
        assert socket.receive_json() == {"step": "connected"}

    row = held(sessions, athlete)
    assert row is not None
    assert garmin.TokenBox((KEY,)).unseal(row.token_ciphertext) == NEW_TOKEN
    # What a copy of the database would show.
    assert b"di_refresh_token" not in row.token_ciphertext
    assert client.get("/api/v1/garmin/connection").status_code == 200


def test_a_login_that_needs_a_code_and_gets_a_wrong_one_first(
    client: TestClient, sessions: Sessions, athlete: uuid.UUID
) -> None:
    with client.websocket_connect(LOGIN, headers=OUR_PAGE) as socket:
        socket.send_json(credentials("mfa@example.com"))
        assert socket.receive_json() == {"step": "code_required"}
        assert held(sessions, athlete) is None

        socket.send_json({"code": "000000"})
        rejected = socket.receive_json()
        assert rejected["step"] == "code_rejected"
        assert "Wrong code" in rejected["detail"]

        socket.send_json({"code": "123456"})
        assert socket.receive_json() == {"step": "connected"}

    assert held(sessions, athlete) is not None


def test_the_password_is_not_kept_while_waiting_for_the_code(
    client: TestClient, athlete: uuid.UUID
) -> None:
    with client.websocket_connect(LOGIN, headers=OUR_PAGE) as socket:
        socket.send_json(credentials("mfa@example.com"))
        assert socket.receive_json() == {"step": "code_required"}
        assert FakeGarmin.made[0].password is None


def test_a_wrong_password_stores_nothing(
    client: TestClient, sessions: Sessions, athlete: uuid.UUID
) -> None:
    with client.websocket_connect(LOGIN, headers=OUR_PAGE) as socket:
        socket.send_json(credentials(password="wrong"))
        failed = socket.receive_json()
    assert failed["step"] == "failed"
    assert "Wrong password" in failed["detail"]
    assert held(sessions, athlete) is None


def test_too_many_wrong_codes_ends_the_login(
    client: TestClient, sessions: Sessions, athlete: uuid.UUID
) -> None:
    with client.websocket_connect(LOGIN, headers=OUR_PAGE) as socket:
        socket.send_json(credentials("mfa@example.com"))
        assert socket.receive_json() == {"step": "code_required"}
        for _ in range(5):
            socket.send_json({"code": "000000"})
            assert socket.receive_json()["step"] == "code_rejected"
        assert socket.receive_json()["step"] == "failed"
    assert held(sessions, athlete) is None


def test_a_connection_dropped_halfway_leaves_nothing_behind(
    client: TestClient, sessions: Sessions, athlete: uuid.UUID
) -> None:
    with client.websocket_connect(LOGIN, headers=OUR_PAGE) as socket:
        socket.send_json(credentials("mfa@example.com"))
        assert socket.receive_json() == {"step": "code_required"}
    # Gone without sending a code. Nothing was stored, so there is nothing to
    # clean up, and the next login starts from the beginning.
    assert held(sessions, athlete) is None
    with client.websocket_connect(LOGIN, headers=OUR_PAGE) as socket:
        socket.send_json(credentials())
        assert socket.receive_json() == {"step": "connected"}


def test_a_new_login_replaces_the_session_held(
    client: TestClient, sessions: Sessions, garmin_session: uuid.UUID
) -> None:
    before = held(sessions, garmin_session)
    assert before is not None
    assert garmin.TokenBox((KEY,)).unseal(before.token_ciphertext) == TOKEN

    with client.websocket_connect(LOGIN, headers=OUR_PAGE) as socket:
        socket.send_json(credentials())
        assert socket.receive_json() == {"step": "connected"}

    after = held(sessions, garmin_session)
    assert after is not None
    assert garmin.TokenBox((KEY,)).unseal(after.token_ciphertext) == NEW_TOKEN
    assert after.version == before.version + 1


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({"Origin": "https://evil.example"}, id="another site's page"),
        pytest.param({}, id="no origin at all"),
    ],
)
def test_only_our_own_page_may_open_it(
    client: TestClient, athlete: uuid.UUID, headers: dict[str, str]
) -> None:
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect(LOGIN, headers=headers),
    ):
        pass
    assert FakeGarmin.made == []


def test_it_needs_an_account(client: TestClient) -> None:
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect(LOGIN, headers=OUR_PAGE),
    ):
        pass
