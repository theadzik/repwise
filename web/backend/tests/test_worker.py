"""The worker: claiming a run, running the command, and what it leaves behind.

The command is a stand-in. No test runs repwise.
"""

import dataclasses
import json
import textwrap
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update

from repwise_web import garmin, worker
from repwise_web.db import Run, Sessions
from repwise_web.settings import Settings
from repwise_web.worker import Worker

from .conftest import KEY, TOKEN

StandIn = Callable[[str], tuple[str, ...]]

REFRESHED = '{"di_token": "t2", "di_refresh_token": "r2", "di_client_id": "c"}'
#: What repwise would do when it refreshes the session: rewrite the file.
REFRESH = textwrap.dedent(
    f"""
    import os
    store = os.path.expanduser("~/.config/repwise/garmin_tokens.json")
    with open(store, "w") as handle:
        handle.write({REFRESHED!r})
    """
)


def queue(client: TestClient) -> uuid.UUID:
    return uuid.UUID(client.post("/api/v1/runs").json()["id"])


def finished(sessions: Sessions, run_id: uuid.UUID) -> Run:
    with sessions() as db:
        run = db.get(Run, run_id)
    assert run is not None
    return run


def session_held(sessions: Sessions, athlete: uuid.UUID) -> tuple[str, int] | None:
    with sessions() as db:
        return garmin.load(db, garmin.TokenBox((KEY,)), athlete)


def test_a_run_is_given_a_home_and_leaves_nothing_in_it(
    client: TestClient,
    settings: Settings,
    sessions: Sessions,
    garmin_session: uuid.UUID,
    stand_in: StandIn,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # What the worker's own environment holds in the cluster.
    secrets = ("DATABASE_URL", "TOKEN_KEYS", "GITHUB_TOKEN")
    for name in secrets:
        monkeypatch.setenv(name, "not for repwise")
    command = stand_in(
        """
        import json, os, stat
        home = os.environ["HOME"]
        store = os.path.join(home, ".config", "repwise")
        token = os.path.join(store, "garmin_tokens.json")
        print(json.dumps({
            "environment": sorted(os.environ),
            "config": open(os.environ["REPWISE_CONFIG"]).read(),
            "token": open(token).read(),
            "token mode": oct(stat.S_IMODE(os.stat(token).st_mode)),
            "store mode": oct(stat.S_IMODE(os.stat(store).st_mode)),
        }))
        """
    )
    run_id = queue(client)

    busy = Worker(dataclasses.replace(settings, command=command), sessions).run_one()

    assert busy
    run = finished(sessions, run_id)
    assert (run.status, run.exit_code, run.error) == ("succeeded", 0, None)
    assert run.started_at is not None
    assert run.finished_at is not None
    seen = json.loads(run.output)
    # Not the worker's own environment, which holds the database password and
    # the key that encrypts every session.
    assert {"HOME", "PATH", "REPWISE_CONFIG"} <= set(seen["environment"])
    assert not set(secrets) & set(seen["environment"])
    assert "garmin_workout_id: 111" in seen["config"]
    assert seen["token"] == TOKEN
    # As garminconnect writes them. repwise warns about anything looser.
    assert (seen["token mode"], seen["store mode"]) == ("0o600", "0o700")
    assert list(Path(settings.scratch_dir).iterdir()) == []
    # The run did not touch the session, so it was not written again.
    assert session_held(sessions, garmin_session) == (TOKEN, 1)


def test_a_failing_run_keeps_its_exit_code_and_what_it_said(
    client: TestClient,
    settings: Settings,
    sessions: Sessions,
    garmin_session: uuid.UUID,
    stand_in: StandIn,
) -> None:
    command = stand_in(
        """
        import sys
        print("to stdout")
        print("to stderr", file=sys.stderr)
        sys.exit(3)
        """
    )
    run_id = queue(client)

    Worker(dataclasses.replace(settings, command=command), sessions).run_one()

    run = finished(sessions, run_id)
    assert (run.status, run.exit_code) == ("failed", 3)
    assert "to stdout" in run.output
    assert "to stderr" in run.output
    # The button works again.
    assert client.post("/api/v1/runs").status_code == 201


def test_a_session_refreshed_by_the_run_is_kept(
    client: TestClient,
    settings: Settings,
    sessions: Sessions,
    garmin_session: uuid.UUID,
    stand_in: StandIn,
) -> None:
    queue(client)

    Worker(dataclasses.replace(settings, command=stand_in(REFRESH)), sessions).run_one()

    assert session_held(sessions, garmin_session) == (REFRESHED, 2)


def wait_then_refresh(
    stand_in: StandIn, tmp_path: Path
) -> tuple[tuple[str, ...], Path]:
    """A command that refreshes the session once a file appears."""
    started, go = tmp_path / "started", tmp_path / "go"
    wait = textwrap.dedent(
        f"""
        import os, time
        open({str(started)!r}, "w").close()
        while not os.path.exists({str(go)!r}):
            time.sleep(0.01)
        """
    )
    return stand_in(wait + REFRESH), go


def in_the_middle_of_a_run(
    settings: Settings, sessions: Sessions, command: tuple[str, ...], tmp_path: Path
) -> threading.Thread:
    """Start a run in the background and return once the command is going."""
    running = threading.Thread(
        target=Worker(dataclasses.replace(settings, command=command), sessions).run_one
    )
    running.start()
    deadline = time.monotonic() + 10
    while not (tmp_path / "started").exists():
        assert time.monotonic() < deadline, "the stand-in never started"
        time.sleep(0.01)
    return running


def test_signing_out_of_garmin_mid_run_is_not_undone_by_it(
    client: TestClient,
    settings: Settings,
    sessions: Sessions,
    garmin_session: uuid.UUID,
    stand_in: StandIn,
    tmp_path: Path,
) -> None:
    command, go = wait_then_refresh(stand_in, tmp_path)
    run_id = queue(client)
    running = in_the_middle_of_a_run(settings, sessions, command, tmp_path)

    assert client.delete("/api/v1/garmin/connection").status_code == 204
    go.touch()
    running.join()

    # The run finished with the session it was given...
    assert finished(sessions, run_id).status == "succeeded"
    # ...and did not put it back.
    assert session_held(sessions, garmin_session) is None


def test_a_login_made_mid_run_is_not_overwritten_by_it(
    client: TestClient,
    settings: Settings,
    sessions: Sessions,
    garmin_session: uuid.UUID,
    stand_in: StandIn,
    tmp_path: Path,
) -> None:
    command, go = wait_then_refresh(stand_in, tmp_path)
    queue(client)
    running = in_the_middle_of_a_run(settings, sessions, command, tmp_path)

    newer = '{"di_token": "from the login"}'
    with sessions() as db, db.begin():
        garmin.store_login(db, garmin.TokenBox((KEY,)), garmin_session, newer)
    go.touch()
    running.join()

    assert session_held(sessions, garmin_session) == (newer, 2)


def test_a_workout_garmin_has_not_built_fails_the_run_before_it_starts(
    client: TestClient,
    settings: Settings,
    sessions: Sessions,
    garmin_session: uuid.UUID,
    stand_in: StandIn,
    config_file: Path,
    tmp_path: Path,
) -> None:
    config_file.write_text(
        "workouts:\n  - key: push\n  - key: pull\n    garmin_workout_id: 2\n"
    )
    ran = tmp_path / "ran"
    command = stand_in(f"open({str(ran)!r}, 'w').close()")
    run_id = queue(client)

    Worker(dataclasses.replace(settings, command=command), sessions).run_one()

    run = finished(sessions, run_id)
    assert run.status == "failed"
    assert run.error is not None
    assert run.error.startswith("push: no garmin_workout_id yet.")
    assert not ran.exists()


def test_a_run_with_no_garmin_session_fails_and_says_so(
    client: TestClient,
    settings: Settings,
    sessions: Sessions,
    garmin_session: uuid.UUID,
) -> None:
    run_id = queue(client)
    # Signed out between the press and a worker getting to it.
    client.delete("/api/v1/garmin/connection")

    Worker(settings, sessions).run_one()

    run = finished(sessions, run_id)
    assert run.status == "failed"
    assert run.error == "Not signed in to Garmin. Sign in on the Garmin screen."


def test_a_config_that_cannot_be_downloaded_fails_the_run(
    client: TestClient,
    settings: Settings,
    sessions: Sessions,
    garmin_session: uuid.UUID,
    config_file: Path,
) -> None:
    config_file.unlink()
    run_id = queue(client)

    Worker(settings, sessions).run_one()

    run = finished(sessions, run_id)
    assert run.status == "failed"
    assert run.error is not None
    assert run.error.startswith("Could not download file://")


def test_a_run_that_cannot_even_be_set_up_is_failed_not_abandoned(
    client: TestClient,
    settings: Settings,
    sessions: Sessions,
    garmin_session: uuid.UUID,
    tmp_path: Path,
) -> None:
    """Left as running, it would hold the button until the heartbeat rule."""
    nowhere = dataclasses.replace(settings, scratch_dir=str(tmp_path / "missing"))
    run_id = queue(client)

    Worker(nowhere, sessions).run_one()

    run = finished(sessions, run_id)
    assert run.status == "failed"
    assert run.error is not None
    assert run.error.startswith("FileNotFoundError:")
    assert client.post("/api/v1/runs").status_code == 201


def test_a_run_that_takes_too_long_is_stopped(
    client: TestClient,
    settings: Settings,
    sessions: Sessions,
    garmin_session: uuid.UUID,
    stand_in: StandIn,
) -> None:
    command = stand_in(
        """
        import time
        print("started", flush=True)
        time.sleep(30)
        """
    )
    run_id = queue(client)

    Worker(
        dataclasses.replace(settings, command=command, run_timeout=1), sessions
    ).run_one()

    run = finished(sessions, run_id)
    assert (run.status, run.exit_code) == ("failed", None)
    assert run.error == "Timed out after 1 seconds"
    assert "started" in run.output


def test_what_a_run_prints_is_shown_while_it_goes(
    client: TestClient,
    settings: Settings,
    sessions: Sessions,
    garmin_session: uuid.UUID,
    stand_in: StandIn,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker, "OUTPUT_SECONDS", 0.05)
    go = tmp_path / "go"
    # Not flushed, as repwise does not flush: what it prints must not be held
    # back until it exits.
    command = stand_in(
        f"""
        import pathlib, time
        print("signed in")
        pathlib.Path({str(tmp_path / "started")!r}).touch()
        while not pathlib.Path({str(go)!r}).exists():
            time.sleep(0.01)
        print("done")
        """
    )
    run_id = queue(client)
    running = in_the_middle_of_a_run(settings, sessions, command, tmp_path)

    try:
        deadline = time.monotonic() + 5
        while not (shown := client.get(f"/api/v1/runs/{run_id}").json())["output"]:
            assert time.monotonic() < deadline, "nothing was shown while the run went"
            time.sleep(0.02)
        assert (shown["status"], shown["output"]) == ("running", "signed in\n")
    finally:
        # Even when the above fails, or the stand-in waits, and the test, forever.
        go.touch()
        running.join()

    run = finished(sessions, run_id)
    assert (run.status, run.output) == ("succeeded", "signed in\ndone\n")


def test_no_run_queued_means_nothing_to_do(
    settings: Settings, sessions: Sessions
) -> None:
    assert Worker(settings, sessions).run_one() is False


def two_athletes_each_with_a_run(sessions: Sessions) -> list[uuid.UUID]:
    """Two queued runs, oldest first. One athlete can only have one."""
    runs = []
    with sessions() as db, db.begin():
        for offset, login in enumerate(("first", "second")):
            athlete, run = uuid.uuid4(), uuid.uuid4()
            db.execute(
                text("INSERT INTO athlete (id, github_login) VALUES (:id, :login)"),
                {"id": athlete, "login": login},
            )
            db.execute(
                text(
                    "INSERT INTO run (id, athlete_id, status, output, created_at) "
                    "VALUES (:id, :athlete, 'queued', '', "
                    "now() + make_interval(secs => :offset))"
                ),
                {"id": run, "athlete": athlete, "offset": offset},
            )
            runs.append(run)
    return runs


def test_the_oldest_run_is_claimed_first_and_only_once(
    settings: Settings, sessions: Sessions
) -> None:
    older, newer = two_athletes_each_with_a_run(sessions)
    one, another = Worker(settings, sessions, "one"), Worker(settings, sessions, "two")

    first, second = one.claim(), another.claim()

    assert first is not None
    assert second is not None
    assert (first[0], second[0]) == (older, newer)
    assert one.claim() is None
    with sessions() as db:
        claimed_by = dict(db.execute(select(Run.id, Run.claimed_by)).all())
    assert claimed_by == {older: "one", newer: "two"}


def test_a_run_another_worker_is_claiming_is_passed_over(
    settings: Settings, sessions: Sessions
) -> None:
    older, newer = two_athletes_each_with_a_run(sessions)

    with sessions() as other_worker, other_worker.begin():
        # Halfway through its own claim of the oldest run.
        other_worker.execute(
            text("SELECT id FROM run WHERE id = :id FOR UPDATE"), {"id": older}
        )
        claimed = Worker(settings, sessions).claim()

    # Not waited for, and not taken twice.
    assert claimed is not None
    assert claimed[0] == newer


def test_a_run_whose_worker_went_quiet_is_failed_and_frees_the_button(
    client: TestClient,
    settings: Settings,
    sessions: Sessions,
    garmin_session: uuid.UUID,
) -> None:
    run_id = queue(client)
    lost = Worker(settings, sessions, "lost")
    assert lost.claim() is not None
    assert client.post("/api/v1/runs").status_code == 409
    survivor = Worker(settings, sessions, "survivor")

    # Still reporting: nothing to do.
    assert survivor.reap() == 0

    with sessions() as db, db.begin():
        db.execute(
            update(Run).values(heartbeat_at=text("now() - interval '61 seconds'"))
        )
    assert survivor.reap() == 1

    run = finished(sessions, run_id)
    assert (run.status, run.error) == ("failed", "Worker lost")
    assert run.finished_at is not None
    assert client.post("/api/v1/runs").status_code == 201


def test_a_run_reports_that_it_is_still_going(
    client: TestClient,
    settings: Settings,
    sessions: Sessions,
    garmin_session: uuid.UUID,
    stand_in: StandIn,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker, "HEARTBEAT_SECONDS", 0.05)
    command = stand_in("import time; time.sleep(0.5)")
    run_id = queue(client)

    Worker(dataclasses.replace(settings, command=command), sessions).run_one()

    run = finished(sessions, run_id)
    assert run.heartbeat_at is not None
    assert run.started_at is not None
    assert run.heartbeat_at > run.started_at


@pytest.mark.parametrize(
    ("token_store", "expected"),
    [
        (None, ".config/repwise/garmin_tokens.json"),
        ("~/.config/repwise", ".config/repwise/garmin_tokens.json"),
        ("~/elsewhere/tokens.json", "elsewhere/tokens.json"),
        ("~", "garmin_tokens.json"),
        # Relative to the working directory repwise is given, which is home.
        ("relative/store", "relative/store/garmin_tokens.json"),
        ("~/a/../b", "b/garmin_tokens.json"),
    ],
)
def test_the_token_goes_where_workouts_yaml_says(
    token_store: str | None, expected: str, tmp_path: Path
) -> None:
    config = {"settings": {"garmin": {"token_store": token_store}}}
    assert worker.token_file(config, tmp_path) == tmp_path.resolve() / expected


@pytest.mark.parametrize(
    "token_store",
    [
        pytest.param("/absolute/store", id="absolute"),
        pytest.param("~/../elsewhere", id="up out of home"),
        pytest.param("../elsewhere", id="relative, up out of home"),
    ],
)
def test_a_token_store_outside_the_runs_home_is_refused(
    token_store: str, tmp_path: Path
) -> None:
    """The session written there would outlive the run, and the pod."""
    config = {"settings": {"garmin": {"token_store": token_store}}}
    with pytest.raises(worker.Refused, match="outside the home a run is given"):
        worker.token_file(config, tmp_path)


def test_a_config_with_no_settings_uses_repwises_default_store(
    tmp_path: Path,
) -> None:
    expected = tmp_path.resolve() / ".config/repwise/garmin_tokens.json"
    assert worker.token_file({}, tmp_path) == expected


def test_a_run_whose_token_store_is_outside_its_home_never_starts(
    client: TestClient,
    settings: Settings,
    sessions: Sessions,
    garmin_session: uuid.UUID,
    stand_in: StandIn,
    config_file: Path,
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    config_file.write_text(
        f"settings:\n  garmin:\n    token_store: {outside}\n"
        "workouts:\n  - key: push\n    garmin_workout_id: 1\n"
    )
    ran = tmp_path / "ran"
    command = stand_in(f"open({str(ran)!r}, 'w').close()")
    run_id = queue(client)

    Worker(dataclasses.replace(settings, command=command), sessions).run_one()

    run = finished(sessions, run_id)
    assert run.status == "failed"
    assert run.error is not None
    assert "outside the home a run is given" in run.error
    assert not ran.exists()
    assert not outside.exists()


def test_repwise_runs_in_its_home(
    client: TestClient,
    settings: Settings,
    sessions: Sessions,
    garmin_session: uuid.UUID,
    stand_in: StandIn,
) -> None:
    """So that a relative path in workouts.yaml is under home for it too."""
    command = stand_in("import os; print(os.getcwd() == os.environ['HOME'])")
    run_id = queue(client)

    Worker(dataclasses.replace(settings, command=command), sessions).run_one()

    assert finished(sessions, run_id).output.strip() == "True"


def test_which_workouts_have_no_garmin_id() -> None:
    config = {
        "workouts": [
            {"key": "a", "garmin_workout_id": 1},
            {"key": "b"},
            {"key": "c", "garmin_workout_id": None},
        ]
    }
    assert worker.without_garmin_id(config) == ["b", "c"]
    assert worker.without_garmin_id({}) == []
