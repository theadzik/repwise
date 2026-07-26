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
