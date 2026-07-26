"""The Garmin session wrapper, against a stub API."""

from workout.garmin.client import STRENGTH, GarminSession
from workout.models import GarminSettings


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


def test_push_always_names_the_device():
    """The library defaults to the last-used device only.

    This tool addresses every device the config selects, so the id has to be
    passed explicitly rather than left to default.
    """
    s, api = writer()

    s.push_workout("111", 4242)

    assert api.pushed == [("111", 4242)]
    assert api.pushed[0][1] is not None, "never rely on the library's default"


def test_pending_messages_takes_the_url_from_the_library():
    """garminconnect has no getter, but the path should not be duplicated."""
    s, api = writer({"messages": [{"messageId": 1}]})

    assert s.pending_messages() == [{"messageId": 1}]
    assert api.requested == [WriteStub.garmin_connect_devicemessage_url]


def test_pending_messages_handles_an_empty_queue():
    s, _ = writer({"numOfMessages": 0})
    assert s.pending_messages() == []
