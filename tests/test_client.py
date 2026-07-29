"""The Garmin session wrapper, against a stub API."""

import pytest
from garminconnect import GarminConnectTooManyRequestsError

from workout.domain.models import GarminSettings
from workout.errors import ExitCode, GarminError, NoTerminal, RateLimited
from workout.garmin.client import STRENGTH, GarminSession, connect


class StubApi:
    """Records calls and serves canned pages."""

    def __init__(self, total: int):
        self.total = total
        self.calls: list[dict] = []

    def connectapi(self, url, params=None):
        self.calls.append(dict(params or {}))
        start = (params or {}).get("start", 0)
        limit = (params or {}).get("limit", 200)
        return [{"workoutId": i} for i in range(start, min(start + limit, self.total))]


def session(total: int) -> tuple[GarminSession, StubApi]:
    api = StubApi(total)
    return GarminSession(api, GarminSettings()), api


def test_a_single_short_page_needs_one_call():
    s, api = session(2)
    assert len(s.list_workouts()) == 2
    assert len(api.calls) == 1


def test_pagination_is_followed_past_a_full_page():
    """Garmin reports no total, so a full page means ask again."""
    s, api = session(271)
    assert len(s.list_workouts(page_size=200)) == 271
    assert [c["start"] for c in api.calls] == [0, 200]


def test_an_exactly_full_last_page_still_terminates():
    s, api = session(400)
    assert len(s.list_workouts(page_size=200)) == 400
    assert [c["start"] for c in api.calls] == [0, 200, 400]


def test_sport_type_is_sent_as_a_server_side_filter():
    s, api = session(1)
    s.list_workouts()
    assert api.calls[0]["sportTypeKey"] == STRENGTH


def test_sport_type_can_be_dropped_for_every_kind():
    s, api = session(1)
    s.list_workouts(sport_type=None)
    assert "sportTypeKey" not in api.calls[0]


# --- writes, delegated to garminconnect -----------------------------------


class WriteStub:
    """Records the library calls the session makes."""

    garmin_connect_devicemessage_url = "/device-service/devicemessage/messages"

    def __init__(self, messages=None):
        self.pushed: list[tuple] = []
        self.updated: list[tuple] = []
        self.requested: list[str] = []
        self._messages = messages or {}

    def push_workout_to_device(self, workout_id, device_id=None):
        self.pushed.append((workout_id, device_id))

    def update_workout(self, workout_id, payload):
        self.updated.append((workout_id, payload))

    def connectapi(self, url, params=None):
        self.requested.append(url)
        return self._messages


def writer(messages=None) -> tuple[GarminSession, WriteStub]:
    api = WriteStub(messages)
    return GarminSession(api, GarminSettings()), api


def test_save_workout_goes_through_update_workout():
    """update_workout keeps the id, so calendar schedules stay valid."""
    s, api = writer()
    payload = {"workoutName": "Workout B"}

    s.save_workout("111", payload)

    assert api.updated == [("111", payload)]


def test_push_lets_the_library_pick_the_last_used_device():
    """No device id is passed, so garminconnect targets the last-used device."""
    s, api = writer()

    s.push_workout("111")

    assert api.pushed == [("111", None)]


def test_pending_messages_takes_the_url_from_the_library():
    """garminconnect has no getter, but the path should not be duplicated."""
    s, api = writer({"messages": [{"messageId": 1}]})

    assert s.pending_messages() == [{"messageId": 1}]
    assert api.requested == [WriteStub.garmin_connect_devicemessage_url]


def test_pending_messages_handles_an_empty_queue():
    s, _ = writer({"numOfMessages": 0})
    assert s.pending_messages() == []


# --- failures, translated at the boundary ---------------------------------


class BrokenApi:
    """Every call fails, with whatever the test asked for."""

    def __init__(self, failure: Exception):
        self.failure = failure

    def __getattr__(self, _name):
        def raise_it(*args, **kwargs):
            raise self.failure

        return raise_it


def broken(failure: Exception) -> GarminSession:
    return GarminSession(BrokenApi(failure), GarminSettings())


def test_a_library_failure_becomes_a_garmin_error():
    """Callers should not have to know what garminconnect can raise."""
    session = broken(ValueError("no JSON could be decoded"))

    with pytest.raises(GarminError, match="Could not fetch the workout"):
        session.workout("111")


def test_the_original_failure_is_kept_as_the_cause():
    original = ValueError("no JSON could be decoded")
    session = broken(original)

    with pytest.raises(GarminError) as caught:
        session.workout("111")

    assert caught.value.__cause__ is original


def test_rate_limiting_keeps_its_own_type_and_exit_code():
    """It is the one failure worth telling apart: waiting is the only fix."""
    session = broken(GarminConnectTooManyRequestsError("429"))

    with pytest.raises(RateLimited) as caught:
        session.recent_activities()

    assert caught.value.exit_code == ExitCode.RATE_LIMITED
    assert "Wait a while" in caught.value.advice


def test_a_write_failure_is_translated_too():
    session = broken(OSError("connection reset"))

    with pytest.raises(GarminError, match="Could not save the workout"):
        session.save_workout("111", {})


# --- logging in -----------------------------------------------------------


def test_no_terminal_to_log_in_from_is_explained(tmp_path, monkeypatch):
    """From cron, or with stdin redirected, input() raises EOFError."""

    def no_stdin(_prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", no_stdin)
    settings = GarminSettings(token_store=str(tmp_path / "absent"))

    with pytest.raises(NoTerminal, match="no terminal to log in from"):
        connect(settings)


def test_refusing_to_prompt_is_a_garmin_error(tmp_path):
    """prompt=False is for callers that cannot answer a question."""
    settings = GarminSettings(token_store=str(tmp_path / "absent"))

    with pytest.raises(GarminError, match="No usable Garmin session"):
        connect(settings, prompt=False)
