"""The dump directory, and when a copy of a session may be believed."""

import json
import os

import pytest

from repwise import dumps
from repwise.errors import UsageError


def activity(activity_id="111", sets=4, reps=40, volume=800.0):
    """A list entry, which is the only place the totals come from."""
    return {
        "activityId": activity_id,
        "activityName": "Training A",
        "totalSets": sets,
        "totalReps": reps,
        "totalVolume": volume,
    }


@pytest.fixture
def cache(tmp_path):
    return dumps.ActivityCache(str(tmp_path))


def file_at(cache, kind, activity_id):
    return os.path.join(cache.directory, f"{kind}-{activity_id}.json")


def filed(cache, entry=None, activity_id="111", executed=None):
    """Put one session in the cache, the way a download would."""
    cache.reconcile([entry or activity(activity_id)])
    cache.store(dumps.ACTIVITY, activity_id, {"activityName": "Training A"})
    cache.store(dumps.SETS, activity_id, {"exerciseSets": []})
    cache.store(dumps.EXECUTED, activity_id, executed if executed is not None else [])


# --- writing and reading --------------------------------------------------


def test_a_payload_makes_the_round_trip(tmp_path):
    dumps.write({"a": 1}, str(tmp_path), dumps.SETS, "111")

    assert dumps.read(str(tmp_path), dumps.SETS, "111") == {"a": 1}


def test_a_payload_never_written_reads_as_nothing(tmp_path):
    assert dumps.read(str(tmp_path), dumps.SETS, "111") is None


def test_a_half_written_payload_reads_as_nothing(tmp_path):
    """An interrupted run should cost a request, not a run."""
    (tmp_path / "sets-111.json").write_text('{"exerciseSets": [')

    assert dumps.read(str(tmp_path), dumps.SETS, "111") is None


def test_a_name_carrying_a_path_is_refused(tmp_path):
    with pytest.raises(UsageError):
        dumps.write({}, str(tmp_path), dumps.SETS, "../escape")


# --- what the cache holds -------------------------------------------------


def test_a_filed_session_is_held(cache):
    filed(cache)

    assert cache.holds("111")
    assert cache.load(dumps.SETS, "111") == {"exerciseSets": []}


def test_a_session_never_filed_is_not_held(cache):
    assert not cache.holds("111")
    assert cache.load(dumps.SETS, "111") is None


def test_deleting_a_file_by_hand_stops_it_being_held(cache):
    """The documented way to force one session to be downloaded again."""
    filed(cache)

    os.remove(file_at(cache, dumps.SETS, "111"))

    assert not cache.holds("111")


def test_a_session_run_against_no_workout_is_filed_as_having_none(cache):
    """Written rather than left out, so that absence means one thing only."""
    filed(cache, executed=[])

    assert os.path.exists(file_at(cache, dumps.EXECUTED, "111"))
    assert cache.load(dumps.EXECUTED, "111") == []


def test_an_executed_workout_is_kept_when_there_is_one(cache):
    filed(cache, executed=[{"stepId": 1}])

    assert cache.load(dumps.EXECUTED, "111") == [{"stepId": 1}]


# --- when Garmin stops agreeing -------------------------------------------


def test_a_session_garmin_still_agrees_with_stays(cache):
    filed(cache)

    cache.reconcile([activity()])

    assert cache.holds("111")


def test_a_session_edited_in_connect_is_dropped(cache):
    """The rep the watch missed and you added afterwards moves the totals."""
    filed(cache)

    cache.reconcile([activity(reps=41, volume=820.0)])

    assert not cache.holds("111")


def test_a_dropped_session_stays_dropped_for_the_next_run(cache):
    """The index is on disk, so a second run does not trust it again."""
    filed(cache)
    cache.reconcile([activity(reps=41)])

    assert not dumps.ActivityCache(cache.directory).holds("111")


def test_a_session_garmin_did_not_mention_is_left_alone(cache):
    """Older than the search limit is not the same as contradicted."""
    filed(cache)

    cache.reconcile([activity(activity_id="999")])

    assert cache.holds("111")


def test_a_session_filed_with_no_totals_is_believed(cache):
    """An id named on the command line is in no list, so nothing contradicts it."""
    for kind in dumps.SESSION:
        cache.store(kind, "111", [])

    assert cache.holds("111")


def test_a_payload_not_yet_asked_for_is_not_assumed(cache):
    """The bug this index shape exists to stop: two of three is not three."""
    cache.store(dumps.ACTIVITY, "111", {})
    cache.store(dumps.SETS, "111", {})

    assert cache.has(dumps.SETS, "111")
    assert not cache.has(dumps.EXECUTED, "111")
    assert not cache.holds("111")
    assert cache.load(dumps.EXECUTED, "111") is None


def test_the_index_survives_a_new_cache_over_the_same_directory(cache):
    filed(cache)

    assert dumps.ActivityCache(cache.directory).holds("111")


def test_the_index_is_named_so_the_ignore_rules_catch_it(cache):
    """It is as much your Garmin data as the payloads beside it."""
    filed(cache)

    assert dumps.INDEX_FILE.startswith("activity-")
    assert os.path.exists(os.path.join(cache.directory, dumps.INDEX_FILE))


def test_an_unreadable_index_is_started_over_rather_than_fatal(tmp_path):
    (tmp_path / dumps.INDEX_FILE).write_text("{not json")

    assert not dumps.ActivityCache(str(tmp_path)).holds("111")


def test_the_index_records_what_garmin_said(cache):
    filed(cache)

    with open(os.path.join(cache.directory, dumps.INDEX_FILE)) as fh:
        assert json.load(fh)["111"]["totals"] == {
            "totalSets": 4,
            "totalReps": 40,
            "totalVolume": 800.0,
        }
