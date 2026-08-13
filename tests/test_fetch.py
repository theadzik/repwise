"""Downloading Garmin's payloads to disk: which ones, and which files."""

import json
import logging
import os
from typing import Any

import pytest

from repwise.app.fetch import cache_activities, run_fetch, run_fetch_activities
from repwise.domain.models import Config, GarminSettings
from repwise.errors import ExitCode, GarminError, UsageError

STRENGTH_TYPE = {"typeId": 13, "typeKey": "strength_training"}
RUNNING_TYPE = {"typeId": 1, "typeKey": "running"}


def activity(activity_id, name="Training A", sport=None):
    return {
        "activityId": activity_id,
        "activityName": name,
        "activityType": sport or STRENGTH_TYPE,
    }


class FakeSession:
    """An account, and a record of what was asked of it."""

    def __init__(self, activities=(), executed=None, failing=()):
        self.activities = list(activities)
        #: Which activities were performed against a workout. Absent means one
        #: that was not, which is what Garmin answers with an empty list.
        self.executed = executed or {}
        #: Ids that cannot be downloaded, so that one failure among several can
        #: be told from all of them failing.
        self.failing = set(failing)
        self.asked: list[str] = []
        self.limits: list[int | None] = []

    def is_cached(self, activity_id):
        """What a session with no dump directory behind it answers."""
        return False

    def recent_activities(self, limit=None):
        self.limits.append(limit)
        return self.activities

    def activity(self, activity_id):
        self.asked.append(str(activity_id))
        if str(activity_id) in self.failing:
            raise GarminError(f"Could not fetch that activity: {activity_id}")
        found = [
            entry
            for entry in self.activities
            if str(entry["activityId"]) == str(activity_id)
        ]
        return found[0] if found else activity(activity_id, name="Unlisted")

    def exercise_sets(self, activity_id):
        return {"exerciseSets": [{"setType": "ACTIVE", "activityId": activity_id}]}

    def executed_workout(self, activity_id):
        return self.executed.get(str(activity_id), [])

    def workout(self, workout_id):
        if str(workout_id) in self.failing:
            raise GarminError(f"Could not fetch the workout: {workout_id}")
        return {"workoutId": workout_id, "workoutName": f"Workout {workout_id}"}


def account(activities=(), executed=None, failing=()) -> Any:
    """A FakeSession, typed as what a use case will accept.

    `Any` rather than the class, so that a stand-in goes where a real session
    is asked for without a `cast` at every call site.
    """
    return FakeSession(activities, executed, failing)


@pytest.fixture
def config(tmp_path):
    return Config(workouts={}, garmin=GarminSettings(dump_dir=str(tmp_path)))


def written(config) -> set[str]:
    return set(os.listdir(config.garmin.dump_dir))


def read(config, name):
    with open(os.path.join(config.garmin.dump_dir, name)) as fh:
        return json.load(fh)


# --- activities -----------------------------------------------------------


def test_a_session_is_saved_as_three_payloads(config):
    session = account([activity("111")], executed={"111": [{"stepId": 1}]})

    assert run_fetch_activities(session, config) == ExitCode.OK
    assert written(config) == {
        "activity-111.json",
        "sets-111.json",
        "executed-111.json",
    }


def test_the_payloads_are_saved_unaltered(config):
    session = account([activity("111")], executed={"111": [{"stepId": 1}]})

    run_fetch_activities(session, config)

    assert read(config, "activity-111.json")["activityName"] == "Training A"
    assert read(config, "sets-111.json")["exerciseSets"][0]["setType"] == "ACTIVE"
    assert read(config, "executed-111.json") == [{"stepId": 1}]


def test_a_session_run_against_no_workout_records_that(config):
    """Written rather than left out: absence has to mean "nobody asked"."""
    session = account([activity("111")])

    assert run_fetch_activities(session, config) == ExitCode.OK
    assert written(config) == {
        "activity-111.json",
        "sets-111.json",
        "executed-111.json",
    }
    assert read(config, "executed-111.json") == []


def test_a_scan_keeps_the_strength_activities(config):
    session = account(
        [
            activity("111"),
            activity("222", name="Morning Run", sport=RUNNING_TYPE),
            activity("333"),
        ]
    )

    assert run_fetch_activities(session, config) == ExitCode.OK
    assert session.asked == ["111", "333"]


def test_a_scan_finding_no_strength_activity_says_so(config, caplog):
    session = account([activity("222", sport=RUNNING_TYPE)])

    assert run_fetch_activities(session, config) == ExitCode.NOTHING_USABLE
    assert written(config) == set()
    assert "activity_search_limit" in caplog.text


def test_a_scan_reaches_as_far_as_the_configured_limit(config):
    """The limit is the session's own default, not one invented here."""
    session = account([activity("111")])

    run_fetch_activities(session, config)

    assert session.limits == [None]


def test_ids_are_downloaded_without_a_scan(config):
    session = account([activity("111")])

    assert run_fetch_activities(session, config, ["999"]) == ExitCode.OK
    assert session.limits == [], "an id names a session, so nothing is searched"
    assert session.asked == ["999"]
    assert written(config) == {
        "activity-999.json",
        "sets-999.json",
        "executed-999.json",
    }


def test_an_id_is_downloaded_whatever_sport_it_was(config):
    """Asking for one by id says more about what you want than its sport does."""
    session = account([activity("222", sport=RUNNING_TYPE)])

    assert run_fetch_activities(session, config, ["222"]) == ExitCode.OK
    assert written(config) == {
        "activity-222.json",
        "sets-222.json",
        "executed-222.json",
    }


def test_one_unreachable_session_does_not_cost_the_others(config, caplog):
    session = account([activity("111"), activity("222")], failing=["111"])

    assert run_fetch_activities(session, config) == ExitCode.NOTHING_USABLE
    assert written(config) == {
        "activity-222.json",
        "sets-222.json",
        "executed-222.json",
    }
    assert "FAILED 111" in caplog.text


def test_the_count_at_the_end_is_of_what_was_saved(config, caplog):
    """Two were asked for and one arrived, so saying two would be a lie."""
    session = account([activity("111"), activity("222")], failing=["111"])

    with caplog.at_level(logging.INFO, logger="repwise.app.fetch"):
        run_fetch_activities(session, config)

    assert "1 session(s)" in caplog.text


def test_an_activity_id_carrying_a_path_is_refused(config):
    """`dump_dir` is where these go, and an id is not a way out of it."""
    session = account([activity("111")])

    with pytest.raises(UsageError):
        run_fetch_activities(session, config, ["../escape"])

    assert written(config) == set()


# --- workouts, which keep the behaviour they had --------------------------


def test_workouts_are_saved_one_file_each(config):
    session = account()

    assert run_fetch(session, config, ["123"]) == ExitCode.OK
    assert written(config) == {"workout-123.json"}
    assert read(config, "workout-123.json")["workoutId"] == "123"


def test_a_workout_id_carrying_a_path_is_refused(config):
    """An absolute one is worse: os.path.join drops the directory entirely."""
    with pytest.raises(UsageError):
        run_fetch(account(), config, ["/tmp/escape"])

    assert written(config) == set()


def test_one_unreachable_workout_does_not_cost_the_others(config, caplog):
    session = account(failing=["123"])

    assert run_fetch(session, config, ["123", "456"]) == ExitCode.NOTHING_USABLE
    assert written(config) == {"workout-456.json"}
    assert "FAILED 123" in caplog.text


# --- what is already on disk ----------------------------------------------


class CachingSession(FakeSession):
    """A session that already holds some sessions, as a cached one would."""

    def __init__(self, activities=(), holds=()):
        super().__init__(activities)
        self.held = {str(each) for each in holds}

    def is_cached(self, activity_id):
        return str(activity_id) in self.held


def holding(activities=(), holds=()) -> Any:
    return CachingSession(activities, holds)


def test_a_session_already_on_disk_is_not_downloaded(config, caplog):
    session = holding([activity("111"), activity("222")], holds=["111"])

    with caplog.at_level(logging.INFO, logger="repwise.app.fetch"):
        assert run_fetch_activities(session, config) == ExitCode.OK

    assert session.asked == ["222"]
    assert "Already on disk: 111" in caplog.text


def test_the_summary_says_how_to_download_them_again(config, caplog):
    session = holding([activity("111"), activity("222")], holds=["111"])

    with caplog.at_level(logging.INFO, logger="repwise.app.fetch"):
        run_fetch_activities(session, config)

    assert "1 session(s)" in caplog.text
    assert "--force" in caplog.text


def test_nothing_is_said_about_force_when_nothing_was_skipped(config, caplog):
    session = holding([activity("111")])

    with caplog.at_level(logging.INFO, logger="repwise.app.fetch"):
        run_fetch_activities(session, config)

    assert "--force" not in caplog.text


# --- filling the directory before a run works anything out ----------------


@pytest.fixture
def caching_config(tmp_path):
    return Config(
        workouts={},
        garmin=GarminSettings(dump_dir=str(tmp_path), activity_caching=True),
    )


def test_nothing_is_filed_when_caching_is_off(config):
    """Otherwise every run would download the whole search limit again."""
    session = holding([activity("111"), activity("222")])

    cache_activities(session, config, session.activities)

    assert session.asked == []
    assert written(config) == set()


def test_every_strength_session_missing_is_filed(caching_config):
    session = holding([activity("111"), activity("222")])

    cache_activities(session, caching_config, session.activities)

    assert session.asked == ["111", "222"]
    assert written(caching_config) == {
        "activity-111.json",
        "sets-111.json",
        "executed-111.json",
        "activity-222.json",
        "sets-222.json",
        "executed-222.json",
    }


def test_a_session_already_filed_is_left_alone(caching_config):
    session = holding([activity("111"), activity("222")], holds=["111"])

    cache_activities(session, caching_config, session.activities)

    assert session.asked == ["222"]


def test_what_was_not_a_strength_session_is_not_filed(caching_config):
    """A run holds no sets to learn from, so there is nothing to keep."""
    session = holding([activity("222", sport=RUNNING_TYPE)])

    cache_activities(session, caching_config, session.activities)

    assert session.asked == []


def test_one_session_that_cannot_be_filed_does_not_stop_the_rest(
    caching_config, caplog
):
    """Filling a cache is not the work; whatever is missing is fetched later."""
    session = holding([activity("111"), activity("222")])
    session.failing = {"111"}

    cache_activities(session, caching_config, session.activities)

    assert "Could not file 111" in caplog.text
    assert written(caching_config) == {
        "activity-222.json",
        "sets-222.json",
        "executed-222.json",
    }
