"""Choosing which sessions to update, and updating more than one at a time."""

import argparse

import pytest
from conftest import spec
from test_payloads import active, rep_step
from test_payloads import workout as steps

from workout.cli import EXIT_OK, Payloads, changed_steps, command_update, pick_sessions
from workout.domain.models import Config, Workout
from workout.planner import ActivityNotFound

SQUAT = spec(sets=3, weight_step=2.5)
CALF = spec(
    name="Weighted Standing Calf Raise",
    garmin_name="WEIGHTED_STANDING_CALF_RAISE",
    garmin_category="CALF_RAISE",
    rep_low=12,
    rep_high=20,
    sets=3,
    load="machine",
    weight_step=5.0,
)
BENCH = spec(
    name="Barbell Bench Press",
    garmin_name="BARBELL_BENCH_PRESS",
    garmin_category="BENCH_PRESS",
    sets=3,
)


class FakeSession:
    """A Garmin account: activities newest first, and workout definitions."""

    def __init__(self, activities, workouts, sets):
        self.activities = activities
        self.workouts = workouts
        self.sets = sets
        self.fetched: list[str] = []
        self.saved: list[tuple[str, dict]] = []
        self.pushed: list[str] = []

    def recent_activities(self, limit=None):
        return self.activities

    def activity(self, activity_id):
        return next(a for a in self.activities if str(a["activityId"]) == activity_id)

    def exercise_sets(self, activity_id):
        return self.sets[str(activity_id)]

    def workout(self, workout_id):
        self.fetched.append(workout_id)
        return self.workouts[workout_id]

    def save_workout(self, workout_id, payload):
        self.saved.append((workout_id, payload))

    def push_workout(self, workout_id):
        self.pushed.append(workout_id)


def an_activity(activity_id, name):
    return {"activityId": activity_id, "activityName": name}


def sets_of(*performed):
    return {"exerciseSets": list(performed)}


def config_ab(a_exercises=(SQUAT, CALF), b_exercises=(BENCH, CALF)):
    return Config(
        {
            "Workout A": Workout("Workout A", "111", ["workout a"], list(a_exercises)),
            "Workout B": Workout("Workout B", "222", ["workout b"], list(b_exercises)),
        }
    )


def args(**overrides):
    base = {"apply": False, "activity": None, "dump": False, "push": False}
    return argparse.Namespace(**{**base, **overrides})


@pytest.fixture
def account():
    """Workout A trained today, Workout B two days ago, both unprocessed."""
    return FakeSession(
        activities=[
            an_activity(900, "Workout A"),
            an_activity(800, "Workout B"),
            an_activity(700, "Workout A"),
        ],
        workouts={
            "111": steps(
                rep_step("BARBELL_BACK_SQUAT", "SQUAT", 7, 30.0),
                rep_step("WEIGHTED_STANDING_CALF_RAISE", "CALF_RAISE", 15, 20.0),
            ),
            "222": steps(
                rep_step("BARBELL_BENCH_PRESS", "BENCH_PRESS", 8, 40.0),
                rep_step("WEIGHTED_STANDING_CALF_RAISE", "CALF_RAISE", 15, 20.0),
            ),
        },
        sets={
            # Both sessions trained the calf raise, which is in both workouts,
            # so the two sessions have to agree on it by the end of the run.
            "900": sets_of(
                *[active("BARBELL_BACK_SQUAT", "SQUAT", 8, 30000.0)] * 3,
                *[active("WEIGHTED_STANDING_CALF_RAISE", "CALF_RAISE", 17, 20000.0)]
                * 3,
            ),
            "800": sets_of(
                *[active("BARBELL_BENCH_PRESS", "BENCH_PRESS", 9, 40000.0)] * 3,
                *[active("WEIGHTED_STANDING_CALF_RAISE", "CALF_RAISE", 15, 20000.0)]
                * 3,
            ),
            "700": sets_of(*[active("BARBELL_BACK_SQUAT", "SQUAT", 6, 30000.0)] * 3),
        },
    )


def calf_targets(saved) -> dict[str, float]:
    """The calf raise target each written workout ended up with."""
    return {
        workout_id: step["endConditionValue"]
        for workout_id, payload in saved
        for step in payload["workoutSegments"][0]["workoutSteps"]
        if step["exerciseName"] == "WEIGHTED_STANDING_CALF_RAISE"
    }


# --- choosing the sessions ------------------------------------------------


def test_every_workout_gets_its_own_latest_session(account):
    """Training A then B and running once should advance both, not just B."""
    chosen = pick_sessions(account, config_ab(), None)
    assert [(w.key, a["activityId"]) for w, a in chosen] == [
        ("Workout B", 800),
        ("Workout A", 900),
    ]


def test_sessions_are_replayed_oldest_first(account):
    """The order the sessions happened in, so the newest has the last word."""
    chosen = pick_sessions(account, config_ab(), None)
    assert [a["activityId"] for _, a in chosen] == [800, 900]


def test_only_the_latest_activity_per_workout_is_used(account):
    """Activity 700 is an older Workout A and stays untouched."""
    chosen = pick_sessions(account, config_ab(), None)
    assert 700 not in [a["activityId"] for _, a in chosen]


def test_a_workout_with_no_activity_is_simply_absent(account):
    """An untrained workout is not an error while another workout matched."""
    config = config_ab()
    config.workouts["Workout C"] = Workout("Workout C", "333", ["workout c"], [SQUAT])

    chosen = pick_sessions(account, config, None)

    assert [w.key for w, _ in chosen] == ["Workout B", "Workout A"]


def test_an_explicit_activity_overrides_the_scan(account):
    """--activity names one session, so only that one is replayed."""
    chosen = pick_sessions(account, config_ab(), "700")
    assert [(w.key, a["activityId"]) for w, a in chosen] == [("Workout A", 700)]


def test_no_matching_activity_at_all_is_an_error(account):
    account.activities = [an_activity(1, "Gdynia Walking")]
    with pytest.raises(ActivityNotFound):
        pick_sessions(account, config_ab(), None)


# --- one payload per workout ----------------------------------------------


def test_a_workout_is_fetched_once_however_often_it_is_asked_for(account):
    payloads = Payloads(account)
    assert payloads["111"] is payloads["111"]
    assert account.fetched == ["111"]


def test_changed_steps_counts_a_twice_moved_step_once():
    """Two sessions deciding one shared exercise is one step changing."""
    from workout.domain.progression import Target
    from workout.planner import Change, Plan

    workout = Workout("Workout A", "111", ["workout a"], [CALF])
    first = Plan(
        workout, {}, [Change(CALF, Target(15, 20.0), Target(16, 20.0), "")], []
    )
    again = Plan(
        workout, {}, [Change(CALF, Target(16, 20.0), Target(17, 20.0), "")], []
    )

    assert len(changed_steps([first, again])) == 1


# --- updating both workouts in one run ------------------------------------


def run(account, monkeypatch, config=None, **overrides):
    monkeypatch.setattr("workout.cli.connect", lambda settings: account)
    return command_update(args(**overrides), config or config_ab())


def test_both_workouts_are_written_in_a_single_run(account, monkeypatch):
    code = run(account, monkeypatch, apply=True)
    assert code == EXIT_OK
    assert sorted(wid for wid, _ in account.saved) == ["111", "222"]


def test_each_workout_is_written_once(account, monkeypatch):
    """A workout is planned from its own session and synced from the other.

    Both mutate the same payload, so one write carries all of it.
    """
    run(account, monkeypatch, apply=True)
    saved = [workout_id for workout_id, _ in account.saved]
    assert len(saved) == len(set(saved))


def test_a_workout_definition_is_fetched_once_per_run(account, monkeypatch):
    """Re-fetching would discard changes an earlier session already applied."""
    run(account, monkeypatch, apply=True)
    assert sorted(account.fetched) == ["111", "222"]


def test_a_shared_exercise_ends_up_at_the_most_recent_decision(account, monkeypatch):
    """The calf raise is in both workouts and must not drift apart.

    Workout B is the older session and moves it 15 -> 16. Workout A is the
    newer one, beats that with 17s, and takes it to 18 - which is what both
    workouts must end up storing.
    """
    run(account, monkeypatch, apply=True)
    assert calf_targets(account.saved) == {"111": 18.0, "222": 18.0}


def test_a_dry_run_writes_nothing(account, monkeypatch):
    assert run(account, monkeypatch) == EXIT_OK
    assert account.saved == []


def test_nothing_is_pushed_without_apply(account, monkeypatch):
    run(account, monkeypatch)
    assert account.pushed == []
