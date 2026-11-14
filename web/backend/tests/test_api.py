"""The REST half of the API: accounts, runs, and signing out of Garmin."""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from starlette.types import Message, Receive, Scope, Send

from repwise_web.api import create_app
from repwise_web.db import FAILED, Run, Sessions
from repwise_web.settings import Settings

from .conftest import SIGNED_IN


def test_registering_is_explicit_and_happens_once(client: TestClient) -> None:
    assert client.get("/api/v1/me").status_code == 404

    first = client.put("/api/v1/me")
    assert first.status_code == 201
    # GitHub logins do not care about case, so neither does the account.
    assert first.json()["github_login"] == "tester"

    again = client.put("/api/v1/me")
    assert again.status_code == 200
    assert again.json()["id"] == first.json()["id"]
    assert client.get("/api/v1/me").json()["id"] == first.json()["id"]


def test_nobody_is_signed_in_without_the_proxys_header(
    settings: Settings, sessions: Sessions
) -> None:
    bare = TestClient(create_app(settings, sessions))
    assert bare.get("/api/v1/me").status_code == 401


def test_an_error_is_a_problem_document(client: TestClient) -> None:
    response = client.get("/api/v1/me")
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "title": "Not Found",
        "status": 404,
        "detail": "No account yet. Register with PUT /api/v1/me.",
    }


def test_a_change_must_be_sent_as_json(settings: Settings, sessions: Sessions) -> None:
    """Which is what a form on another site's page cannot do."""
    form = TestClient(
        create_app(settings, sessions), headers={"X-Forwarded-User": "tester"}
    )
    assert form.put("/api/v1/me").status_code == 415
    assert form.post("/api/v1/runs").status_code == 415
    assert form.delete("/api/v1/garmin/connection").status_code == 415
    # Reading is not a change.
    assert form.get("/api/v1/me").status_code == 404


def test_a_run_needs_a_garmin_session(client: TestClient, athlete: uuid.UUID) -> None:
    refused = client.post("/api/v1/runs")
    assert refused.status_code == 409
    assert "Garmin" in refused.json()["detail"]


def test_one_run_at_a_time(client: TestClient, garmin_session: uuid.UUID) -> None:
    first = client.post("/api/v1/runs")
    assert first.status_code == 201
    assert first.json()["status"] == "queued"
    assert first.headers["location"] == f"/api/v1/runs/{first.json()['id']}"

    second = client.post("/api/v1/runs")
    assert second.status_code == 409
    # Where the run that is in the way can be watched.
    assert second.headers["location"] == first.headers["location"]


def test_two_presses_at_once_make_one_run(
    settings: Settings, sessions: Sessions, garmin_session: uuid.UUID
) -> None:
    """Through two replicas, which share nothing but the database."""
    replicas = [
        TestClient(create_app(settings, sessions), headers=SIGNED_IN) for _ in range(2)
    ]
    for _ in range(5):
        together = threading.Barrier(2)

        def press(replica: TestClient, together: threading.Barrier = together) -> int:
            together.wait()
            return replica.post("/api/v1/runs").status_code

        with ThreadPoolExecutor(2) as pool:
            answers = sorted(pool.map(press, replicas))
        assert answers == [201, 409]

        with sessions() as db, db.begin():
            db.execute(update(Run).values(status=FAILED))


def test_runs_are_listed_newest_first_without_their_output(
    client: TestClient, sessions: Sessions, garmin_session: uuid.UUID
) -> None:
    older = client.post("/api/v1/runs").json()["id"]
    with sessions() as db, db.begin():
        db.execute(update(Run).values(status=FAILED, output="what repwise said"))
    newer = client.post("/api/v1/runs").json()["id"]

    listed = client.get("/api/v1/runs").json()
    assert [run["id"] for run in listed] == [newer, older]
    assert "output" not in listed[0]

    assert client.get(f"/api/v1/runs/{older}").json()["output"] == "what repwise said"
    assert len(client.get("/api/v1/runs", params={"limit": 1}).json()) == 1


def test_a_run_is_only_its_own_athletes(
    client: TestClient,
    settings: Settings,
    sessions: Sessions,
    garmin_session: uuid.UUID,
) -> None:
    run = client.post("/api/v1/runs").json()["id"]

    someone_else = TestClient(
        create_app(settings, sessions),
        headers={**SIGNED_IN, "X-Forwarded-User": "someone-else"},
    )
    someone_else.put("/api/v1/me")
    assert someone_else.get(f"/api/v1/runs/{run}").status_code == 404
    assert someone_else.get("/api/v1/runs").json() == []
    assert someone_else.get("/api/v1/garmin/connection").status_code == 404


def test_signing_out_of_garmin_forgets_the_session(
    client: TestClient, garmin_session: uuid.UUID
) -> None:
    assert client.get("/api/v1/garmin/connection").status_code == 200

    assert client.delete("/api/v1/garmin/connection").status_code == 204
    assert client.get("/api/v1/garmin/connection").status_code == 404
    # Nothing to run with, so nothing is queued.
    assert client.post("/api/v1/runs").status_code == 409
    # Signing out twice is not an error.
    assert client.delete("/api/v1/garmin/connection").status_code == 204


def test_a_change_is_committed_before_it_is_answered(
    settings: Settings, sessions: Sessions, garmin_session: uuid.UUID
) -> None:
    """Or the page, told 201, asks another replica and finds nothing there.

    It also means a commit that fails is reported, not answered with a 201.
    """
    app = create_app(settings, sessions)
    seen = []

    async def watching(scope: Scope, receive: Receive, send: Send) -> None:
        async def answer(message: Message) -> None:
            if message["type"] == "http.response.start":
                # What any other connection can see as the answer goes out.
                with sessions() as db:
                    seen.append(db.scalar(select(func.count()).select_from(Run)))
            await send(message)

        await app(scope, receive, answer)

    assert (
        TestClient(watching, headers=SIGNED_IN).post("/api/v1/runs").status_code == 201
    )
    assert seen == [1]


def test_the_page_can_tell_the_service_is_up(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").json() == {"status": "ok"}
