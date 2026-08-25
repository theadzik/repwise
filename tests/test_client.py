"""The Garmin session wrapper, against a stub API."""

import logging
import os
import stat
from pathlib import Path

import pytest
from garminconnect import GarminConnectTooManyRequestsError

from repwise.domain.models import GarminSettings
from repwise.errors import (
    ExitCode,
    GarminError,
    NoTerminal,
    RateLimited,
    UnsafeTokenStore,
)
from repwise.garmin import client
from repwise.garmin.client import (
    STRENGTH,
    CachedSession,
    GarminSession,
    cached_token,
    connect,
    forget,
)

#: The permission checks are about POSIX modes; what Windows reports is not one.
posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX modes")


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


# --- the token store ------------------------------------------------------


def token_store(tmp_path, mode: int = 0o600, store_mode: int = 0o700):
    """A store holding a cached token, at the modes the test wants to check.

    The token itself is unusable on purpose: every test here stops at
    `prompt=False`, and none of them should be able to reach Garmin.
    """
    store = tmp_path / "tokens"
    store.mkdir()
    (store / "garmin_tokens.json").write_text("{}")
    (store / "garmin_tokens.json").chmod(mode)
    store.chmod(store_mode)
    return GarminSettings(token_store=str(store))


def warnings_from(settings, caplog) -> str:
    """What `connect` warned about on its way to giving up."""
    with (
        caplog.at_level(logging.WARNING, logger="repwise.garmin.client"),
        pytest.raises(GarminError),
    ):
        connect(settings, prompt=False)
    return caplog.text


@posix_only
def test_a_token_file_other_users_can_read_is_warned_about(tmp_path, caplog):
    """The library writes it 0600; a backup or a copy need not have kept that."""
    warned = warnings_from(token_store(tmp_path, mode=0o644), caplog)

    assert "currently 644" in warned
    assert "chmod 600" in warned


@posix_only
def test_a_store_directory_others_can_reach_is_warned_about_too(tmp_path, caplog):
    warned = warnings_from(token_store(tmp_path, store_mode=0o755), caplog)

    assert "chmod 700" in warned


@posix_only
def test_a_private_store_is_not_mentioned_at_all(tmp_path, caplog):
    assert "chmod" not in warnings_from(token_store(tmp_path), caplog)


def test_a_first_run_has_no_permissions_to_complain_about(tmp_path, caplog):
    """Nothing cached yet is a first login, not a problem to report."""
    settings = GarminSettings(token_store=str(tmp_path / "absent"))

    assert "chmod" not in warnings_from(settings, caplog)


@posix_only
def test_a_store_holding_only_the_catalog_is_left_alone(tmp_path, caplog):
    """`fetch exercises` makes one before any login; there is no token in it."""
    store = tmp_path / "tokens"
    store.mkdir()
    (store / "exercises.json").write_text("{}")
    store.chmod(0o755)

    settings = GarminSettings(token_store=str(store))

    assert "chmod" not in warnings_from(settings, caplog)


@posix_only
def test_the_mode_is_reported_but_never_repaired(tmp_path, caplog):
    """Changing the mode of a file nobody asked us to touch is not our business."""
    settings = token_store(tmp_path, mode=0o644)

    warnings_from(settings, caplog)

    token = Path(settings.token_store) / "garmin_tokens.json"
    assert stat.S_IMODE(token.stat().st_mode) == 0o644


# --- a store the library will not touch -----------------------------------


@posix_only
def test_a_symlinked_store_is_refused_rather_than_followed(tmp_path):
    """garminconnect will not read or write through one, so nor do we."""
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "store"
    link.symlink_to(real)

    with pytest.raises(UnsafeTokenStore) as refused:
        cached_token(str(link))

    assert str(link) in str(refused.value), "the path it will not use"


@posix_only
def test_a_symlink_above_the_store_is_refused_too(tmp_path):
    """The realistic one: ~/.config itself linked into a dotfiles checkout."""
    real = tmp_path / "dotfiles" / "config"
    real.mkdir(parents=True)
    (tmp_path / ".config").symlink_to(real)

    with pytest.raises(UnsafeTokenStore):
        cached_token(str(tmp_path / ".config" / "repwise"))


@posix_only
def test_the_refusal_says_what_to_change(tmp_path):
    """It is a config error because the setting is what the user can act on."""
    link = tmp_path / "store"
    link.symlink_to(tmp_path)

    with pytest.raises(UnsafeTokenStore) as refused:
        cached_token(str(link))

    assert refused.value.exit_code == ExitCode.CONFIG
    assert "token_store" in refused.value.advice


def test_a_home_that_cannot_be_expanded_is_refused_the_same_way(monkeypatch):
    """`~` with nothing to expand it against - a uid with no passwd entry.

    Provoked rather than arranged: unsetting $HOME is not enough, because
    `Path.expanduser()` falls back to the passwd database, and a test cannot
    take that away from the machine running it. What is checked is the
    translation, which is this module's, and not the library's rule for when
    to raise.
    """

    def no_home(path: str):
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr(client, "token_file_path", no_home)

    with pytest.raises(UnsafeTokenStore) as refused:
        cached_token("~/tokens")

    assert "~/tokens" in str(refused.value), "the store it could not place"
    assert "home directory" in str(refused.value), "and why it could not"


def test_a_real_store_is_still_just_a_path(tmp_path):
    """The refusals above are the exception; the ordinary answer is unchanged."""
    store = token_store(tmp_path)

    assert cached_token(store.token_store) == str(
        Path(store.token_store) / "garmin_tokens.json"
    )


# --- signing out ----------------------------------------------------------


def test_forget_deletes_the_cached_token(tmp_path):
    settings = token_store(tmp_path)

    deleted = forget(settings)

    assert deleted is not None
    assert not os.path.exists(deleted)


def test_forget_leaves_the_exercise_catalog_alone(tmp_path):
    """A disposable copy of a public file: signing out is no reason to lose it."""
    settings = token_store(tmp_path)
    catalog = Path(settings.token_store) / "exercises.json"
    catalog.write_text("{}")

    forget(settings)

    assert catalog.exists()


def test_forget_with_nothing_cached_reports_nothing_deleted(tmp_path):
    """So the command can tell "signed you out" from "you were not signed in"."""
    settings = GarminSettings(token_store=str(tmp_path / "absent"))

    assert forget(settings) is None


# --- weigh-ins ------------------------------------------------------------


class ScaleStub:
    """Body composition, in the shape and units Garmin really returns it."""

    def __init__(self, body):
        self.body = body
        self.asked: list[tuple[str, str]] = []

    def get_body_composition(self, startdate, enddate):
        self.asked.append((startdate, enddate))
        return self.body


def weighing(body) -> tuple[GarminSession, ScaleStub]:
    api = ScaleStub(body)
    return GarminSession(api, GarminSettings()), api


def test_the_window_average_is_preferred_and_converted_from_grams():
    """A single weigh-in carries a kilo of noise; the average does not."""
    s, api = weighing({"totalAverage": {"weight": 81000.0}})

    assert s.bodyweight() == 81.0
    start, end = api.asked[0]
    assert start < end


def test_a_weigh_in_outside_the_window_is_better_than_nothing():
    s, _ = weighing(
        {"totalAverage": {"weight": None}, "dateWeightList": [{"weight": 79500.0}]}
    )

    assert s.bodyweight() == 79.5


def test_an_account_that_has_never_weighed_in_reads_as_unknown():
    """Not a failure: it just means the range checks cannot run."""
    s, _ = weighing({"totalAverage": {"weight": None}, "dateWeightList": []})

    assert s.bodyweight() is None


# --- a session that reads dump_dir before it asks -------------------------


class ActivityStub:
    """One account's sessions, and a count of what was actually asked for."""

    def __init__(self, activities=()):
        self.activities = list(activities)
        self.asked: list[str] = []

    def get_activities(self, start, limit):
        return self.activities

    def get_activity(self, activity_id):
        self.asked.append(f"activity {activity_id}")
        return {"activityId": activity_id, "activityName": "Training A"}

    def get_activity_exercise_sets(self, activity_id):
        self.asked.append(f"sets {activity_id}")
        return {"exerciseSets": [{"setType": "ACTIVE"}]}

    def connectapi(self, url, params=None):
        self.asked.append(f"executed {url}")
        return []


def listed(activity_id="111", reps=40):
    return {
        "activityId": activity_id,
        "activityName": "Training A",
        "totalSets": 4,
        "totalReps": reps,
        "totalVolume": 800.0,
    }


def caching(tmp_path, activities=()) -> tuple[CachedSession, ActivityStub]:
    api = ActivityStub(activities)
    settings = GarminSettings(dump_dir=str(tmp_path), activity_caching=True)
    return CachedSession(api, settings), api


def test_the_first_read_of_a_session_asks_garmin(tmp_path):
    s, api = caching(tmp_path, [listed()])

    s.recent_activities()
    s.activity("111")
    s.exercise_sets("111")
    s.executed_workout("111")

    assert len(api.asked) == 3


def test_the_second_read_of_a_session_asks_nobody(tmp_path):
    s, api = caching(tmp_path, [listed()])
    s.recent_activities()
    s.activity("111")
    s.exercise_sets("111")
    s.executed_workout("111")
    api.asked.clear()

    assert s.activity("111")["activityName"] == "Training A"
    assert s.exercise_sets("111")["exerciseSets"][0]["setType"] == "ACTIVE"
    assert s.executed_workout("111") == []
    assert api.asked == [], "everything was already on disk"


def test_a_session_edited_in_connect_is_read_again(tmp_path):
    """Fixing a rep count in Connect should reach the next run's targets."""
    s, api = caching(tmp_path, [listed()])
    s.recent_activities()
    s.exercise_sets("111")
    s.activity("111")

    later, api = caching(tmp_path, [listed(reps=41)])
    later.recent_activities()
    later.exercise_sets("111")

    assert api.asked == ["sets 111"]


def test_a_plain_session_holds_nothing(tmp_path):
    """`is_cached` is asked by callers that only want to report a skip."""
    plain = GarminSession(ActivityStub(), GarminSettings(dump_dir=str(tmp_path)))

    assert plain.is_cached("111") is False


def test_a_cached_session_says_what_it_holds(tmp_path):
    s, _ = caching(tmp_path, [listed()])
    s.recent_activities()

    assert s.is_cached("111") is False

    s.activity("111")
    s.exercise_sets("111")

    assert s.is_cached("111") is False, "the executed workout is still unasked"

    s.executed_workout("111")

    assert s.is_cached("111") is True


class LoginStub(ActivityStub):
    """An account whose cached token still works, so `connect` resumes."""

    def login(self, store):
        return True


def connecting(monkeypatch, tmp_path, **settings):
    """Let `connect` resume without a network, and say what -v was told."""
    monkeypatch.setattr(client, "Garmin", lambda *a, **k: LoginStub())
    store = tmp_path / "store"
    store.mkdir()
    store.chmod(0o700)
    return GarminSettings(token_store=str(store), dump_dir=str(tmp_path), **settings)


def test_a_run_that_reads_nothing_says_why(monkeypatch, tmp_path, caplog):
    """A run downloading everything looks the same however it got that way."""
    settings = connecting(monkeypatch, tmp_path)

    with caplog.at_level(logging.DEBUG, logger="repwise.garmin.client"):
        session = connect(settings)

    assert not isinstance(session, CachedSession)
    assert "activity_caching is off" in caplog.text


def test_a_run_that_reads_dump_dir_says_so(monkeypatch, tmp_path, caplog):
    settings = connecting(monkeypatch, tmp_path, activity_caching=True)

    with caplog.at_level(logging.DEBUG, logger="repwise.garmin.client"):
        session = connect(settings)

    assert isinstance(session, CachedSession)
    assert "are read from there" in caplog.text


def test_force_is_not_reported_as_the_setting_being_off(monkeypatch, tmp_path, caplog):
    settings = connecting(monkeypatch, tmp_path, activity_caching=True)

    with caplog.at_level(logging.DEBUG, logger="repwise.garmin.client"):
        connect(settings, cache=False)

    assert "--force" in caplog.text
    assert "activity_caching is off" not in caplog.text
