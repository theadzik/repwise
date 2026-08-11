"""Choosing which sessions to update, and updating more than one at a time."""

import logging
import os
from dataclasses import replace

import pytest
from builders import active, rep_step, repeat, rest_step, spec
from builders import workout as steps

from repwise.app.update import (
    Payloads,
    UpdateOptions,
    changed_steps,
    gather_history,
    pick_sessions,
    run_update,
    sessions_before,
)
from repwise.config import ConfigError
from repwise.domain.models import Config, Workout
from repwise.errors import ActivityNotFound, ExitCode, GarminError, UsageError
from repwise.garmin.payloads import performed_sets

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

    #: What Garmin calls the next workout it is asked to create.
    next_id = "555"

    def __init__(self, activities, workouts, sets, executed=None):
        self.activities = activities
        self.workouts = workouts
        self.sets = sets
        #: The workout each activity was performed against, as Garmin keeps it
        #: beside the activity. Absent means "not performed against a workout",
        #: which is what an account with no such record answers.
        self.executed = executed or {}
        self.read_back: list[str] = []
        self.fetched: list[str] = []
        self.saved: list[tuple[str, dict]] = []
        self.created: list[dict] = []
        self.pushed: list[str] = []
        self.queue_reads = 0
        self.queue_failure: Exception | None = None

    def create_workout(self, payload):
        self.created.append(payload)
        return self.next_id

    def recent_activities(self, limit=None):
        return self.activities

    def activity(self, activity_id):
        return next(a for a in self.activities if str(a["activityId"]) == activity_id)

    def exercise_sets(self, activity_id):
        return self.sets.get(str(activity_id), {"exerciseSets": []})

    def executed_workout(self, activity_id):
        self.read_back.append(str(activity_id))
        return self.executed.get(str(activity_id), [])

    def workout(self, workout_id):
        self.fetched.append(workout_id)
        return self.workouts[workout_id]

    def save_workout(self, workout_id, payload):
        self.saved.append((workout_id, payload))

    def push_workout(self, workout_id):
        self.pushed.append(workout_id)

    def pending_messages(self):
        self.queue_reads += 1
        if self.queue_failure:
            raise self.queue_failure
        return [{"messageId": i} for i, _ in enumerate(self.pushed)]


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
    return UpdateOptions(**overrides)


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
    chosen = pick_sessions(account, config_ab(), None, account.activities)
    assert [(t.workout.key, t.activity["activityId"]) for t in chosen] == [
        ("Workout B", 800),
        ("Workout A", 900),
    ]


def test_sessions_are_replayed_oldest_first(account):
    """The order the sessions happened in, so the newest has the last word."""
    chosen = pick_sessions(account, config_ab(), None, account.activities)
    assert [t.activity["activityId"] for t in chosen] == [800, 900]


def test_only_the_latest_activity_per_workout_is_used(account):
    """Activity 700 is an older Workout A and stays untouched."""
    chosen = pick_sessions(account, config_ab(), None, account.activities)
    assert 700 not in [t.activity["activityId"] for t in chosen]


def test_a_workout_with_no_activity_is_simply_absent(account):
    """An untrained workout is not an error while another workout matched."""
    config = config_ab()
    config.workouts["Workout C"] = Workout("Workout C", "333", ["workout c"], [SQUAT])

    chosen = pick_sessions(account, config, None, account.activities)

    assert [t.workout.key for t in chosen] == ["Workout B", "Workout A"]


def test_an_explicit_activity_overrides_the_scan(account):
    """--activity names one session, so only that one is replayed."""
    chosen = pick_sessions(account, config_ab(), "700", account.activities)
    assert [(t.workout.key, t.activity["activityId"]) for t in chosen] == [
        ("Workout A", 700)
    ]


def test_no_matching_activity_at_all_is_an_error(account):
    account.activities = [an_activity(1, "Gdynia Walking")]
    with pytest.raises(ActivityNotFound):
        pick_sessions(account, config_ab(), None, account.activities)


# --- one payload per workout ----------------------------------------------


def test_a_workout_is_fetched_once_however_often_it_is_asked_for(account):
    payloads = Payloads(account)
    assert payloads["111"] is payloads["111"]
    assert account.fetched == ["111"]


def test_changed_steps_counts_a_twice_moved_step_once():
    """Two sessions deciding one shared exercise is one step changing."""
    from repwise.domain.progression import Target
    from repwise.planner import Change, Plan

    workout = Workout("Workout A", "111", ["workout a"], [CALF])
    first = Plan(
        workout, {}, [Change(CALF, Target(15, 20.0), Target(16, 20.0), "")], []
    )
    again = Plan(
        workout, {}, [Change(CALF, Target(16, 20.0), Target(17, 20.0), "")], []
    )

    assert len(changed_steps([first, again])) == 1


# --- updating both workouts in one run ------------------------------------


def run(account, config=None, **overrides):
    """The use case takes its session as an argument, so nothing is patched."""
    return run_update(account, config or config_ab(), args(**overrides))


def test_both_workouts_are_written_in_a_single_run(account):
    code = run(account, apply=True)
    assert code == ExitCode.OK
    assert sorted(wid for wid, _ in account.saved) == ["111", "222"]


def test_each_workout_is_written_once(account):
    """A workout is planned from its own session and synced from the other.

    Both mutate the same payload, so one write carries all of it.
    """
    run(account, apply=True)
    saved = [workout_id for workout_id, _ in account.saved]
    assert len(saved) == len(set(saved))


def test_a_workout_definition_is_fetched_once_per_run(account):
    """Re-fetching would discard changes an earlier session already applied."""
    run(account, apply=True)
    assert sorted(account.fetched) == ["111", "222"]


def test_a_shared_exercise_ends_up_at_the_most_recent_decision(account):
    """The calf raise is in both workouts and must not drift apart.

    Workout B is the older session and moves it 15 -> 16. Workout A is the
    newer one, beats that with 17s, and takes it to 18 - which is what both
    workouts must end up storing.
    """
    run(account, apply=True)
    assert calf_targets(account.saved) == {"111": 18.0, "222": 18.0}


def test_a_dry_run_writes_nothing(account):
    assert run(account) == ExitCode.OK
    assert account.saved == []


# --- workouts Garmin does not hold yet -------------------------------------


NEW_WORKOUT = """\
workouts:
  - key: Workout C
    activity_prefixes: ["workout c"]
    exercises:
      - name: Barbell Back Squat
        garmin_name: BARBELL_BACK_SQUAT
        garmin_category: SQUAT
        rep_low: 6
        rep_high: 10
        sets: 3
        load: barbell
        weight_step: 2.5
        start_weight: 40
"""


@pytest.fixture
def uncreated(write_config):
    """A config naming one workout that Garmin has never heard of."""
    from repwise.config import load_config

    return load_config(write_config(NEW_WORKOUT))


def test_a_workout_with_no_id_is_created(account, uncreated):
    code = run(account, uncreated, apply=True)

    assert code == ExitCode.OK
    assert len(account.created) == 1, "one create, not a save"
    assert account.saved == []

    payload = account.created[0]
    assert payload["workoutName"] == "Workout C"
    steps = payload["workoutSegments"][0]["workoutSteps"]
    assert steps[0]["workoutSteps"][0]["exerciseName"] == "BARBELL_BACK_SQUAT"
    assert steps[0]["numberOfIterations"] == 3
    assert steps[0]["workoutSteps"][0]["endConditionValue"] == 6.0, "starts at rep_low"
    assert steps[0]["workoutSteps"][0]["weightValue"] == 40.0, "and at start_weight"


def test_the_id_garmin_issues_is_written_back_to_the_config(account, uncreated):
    run(account, uncreated, apply=True)

    with open(uncreated.path) as fh:
        after = fh.read()
    assert f"garmin_workout_id: '{account.next_id}'" in after
    assert uncreated["Workout C"].garmin_workout_id == account.next_id, (
        "and the run carries on with it, so a push or a sync can find it"
    )


def test_a_dry_run_creates_nothing_and_writes_to_no_file(account, uncreated):
    with open(uncreated.path) as fh:
        before = fh.read()

    code = run(account, uncreated)

    assert code == ExitCode.OK
    assert account.created == []
    with open(uncreated.path) as fh:
        assert fh.read() == before


def test_a_config_that_cannot_be_updated_names_the_id_it_lost(account, uncreated):
    """The workout exists in Garmin now. Failing quietly here would mean the
    next run built a second copy of it."""
    os.remove(uncreated.path)

    with pytest.raises(ConfigError) as caught:
        run(account, uncreated, apply=True)

    message = str(caught.value)
    assert account.next_id in message
    assert "will create a second copy" in message


def test_a_created_workout_can_be_pushed_to_the_watch(account, uncreated):
    run(account, uncreated, apply=True, push=True)
    assert account.pushed == [account.next_id]


# --- how the summary reads ------------------------------------------------


def with_a_gap(account, seconds=45):
    """Workout A as Garmin really stores one, and a config that retimes the
    lap-button wait between its two exercises."""
    account.workouts["111"] = steps(
        repeat(rep_step("BARBELL_BACK_SQUAT", "SQUAT", 7, 30.0), sets=SQUAT.sets),
        rest_step(60.0),
        repeat(
            rep_step("WEIGHTED_STANDING_CALF_RAISE", "CALF_RAISE", 15, 20.0),
            sets=CALF.sets,
        ),
    )
    config = config_ab()
    config.workouts["Workout A"] = replace(config["Workout A"], rest_between=seconds)
    return config


def test_the_gap_summary_counts_what_it_is_counting(account, caplog):
    """A bare number reads as an unfinished sentence, and does not match the
    dry run's wording for the same thing."""
    config = with_a_gap(account)

    with caplog.at_level(logging.INFO, logger="repwise.app.update"):
        run(account, config, apply=True)

    assert "Set the rest between exercises in 1 workout(s)." in caplog.text


def test_the_dry_run_says_the_same_thing_the_other_way_round(account, caplog):
    config = with_a_gap(account)

    with caplog.at_level(logging.INFO, logger="repwise.app.update"):
        run(account, config)

    assert "the rest between exercises would change in 1 workout(s)" in caplog.text


# --- shaping a workout no session touched ---------------------------------


def test_a_workout_with_no_session_is_still_brought_in_line(account):
    """A config edit should not have to wait until that workout is next
    trained. Workout B has no activity here; its shape is applied anyway."""
    account.activities = [an_activity(900, "Workout A")]
    config = config_ab(b_exercises=(BENCH,))  # the calf raise is gone from B

    run(account, config, apply=True)

    saved = dict(account.saved)
    assert "222" in saved, "Workout B was written without having been trained"
    names = [
        step["exerciseName"]
        for step in saved["222"]["workoutSegments"][0]["workoutSteps"]
    ]
    assert names == ["BARBELL_BENCH_PRESS"], "the calf raise was dropped"


def test_no_activity_at_all_still_shapes_the_workouts(account, caplog):
    """It used to end the run. There is config-driven work to do regardless."""
    account.activities = [an_activity(1, "Gdynia Walking")]
    config = config_ab(a_exercises=(SQUAT,))  # the calf raise is gone from A

    with caplog.at_level(logging.INFO, logger="repwise.app.update"):
        code = run(account, config, apply=True)

    assert code == ExitCode.OK
    assert "111" in dict(account.saved)
    assert "Shaping the workouts from the config regardless" in caplog.text


def test_no_activity_and_nothing_to_shape_is_still_an_error(account):
    """With neither a session nor a config change, the missing activity is the
    only thing worth saying."""
    account.activities = [an_activity(1, "Gdynia Walking")]
    account.workouts["111"] = steps(
        repeat(rep_step("BARBELL_BACK_SQUAT", "SQUAT", 7, 30.0), sets=SQUAT.sets)
    )
    config = Config({"Workout A": Workout("Workout A", "111", ["workout a"], [SQUAT])})
    for group in account.workouts["111"]["workoutSegments"][0]["workoutSteps"]:
        group["workoutSteps"][0]["description"] = SQUAT.note

    with pytest.raises(ActivityNotFound):
        run(account, config, apply=True)


# --- rest times -----------------------------------------------------------


@pytest.fixture
def rested(account):
    """Workout A with the squat in a repeat group resting 120s between sets."""
    account.workouts["111"] = steps(
        repeat(rep_step("BARBELL_BACK_SQUAT", "SQUAT", 7, 30.0), sets=3, rest=120.0),
        rep_step("WEIGHTED_STANDING_CALF_RAISE", "CALF_RAISE", 15, 20.0),
    )
    return account


def saved_rest(saved, workout_id):
    """The rest the written workout ended up prescribing between squat sets."""
    payload = next(p for wid, p in saved if wid == workout_id)
    group = payload["workoutSegments"][0]["workoutSteps"][0]
    return group["workoutSteps"][1]["endConditionValue"]


def test_a_configured_rest_is_written_with_the_targets(rested):
    """One save carries the rest and the target alike."""
    config = config_ab(a_exercises=(replace(SQUAT, rest=150), CALF))
    run(rested, config, apply=True)

    assert saved_rest(rested.saved, "111") == 150.0


def test_a_dry_run_leaves_the_rest_alone(rested, caplog):
    config = config_ab(a_exercises=(replace(SQUAT, rest=150), CALF))

    with caplog.at_level(logging.INFO, logger="repwise.app.update"):
        run(rested, config)

    assert rested.saved == []
    assert "1 rest time(s) would change" in caplog.text


def test_a_rest_alone_is_reason_enough_to_write(rested):
    """No session moves a rest, so nothing else need have changed."""
    config = Config(
        {
            "Workout A": Workout(
                "Workout A", "111", ["workout a"], [replace(SQUAT, rest=150)]
            )
        }
    )
    rested.sets["900"] = sets_of(
        *[active("BARBELL_BACK_SQUAT", "SQUAT", 6, 30000.0)] * 3
    )

    code = run(rested, config, apply=True)

    assert code == ExitCode.OK
    assert [wid for wid, _ in rested.saved] == ["111"]
    assert saved_rest(rested.saved, "111") == 150.0


def saved_skip(saved, workout_id):
    """Whether the written workout still drops the squat's last rest."""
    payload = next(p for wid, p in saved if wid == workout_id)
    return payload["workoutSegments"][0]["workoutSteps"][0]["skipLastRestStep"]


def test_a_group_skipping_its_last_rest_is_written_back_resting(rested, caplog):
    """Connect's switch is the one place a set ends without its rest."""
    rested.workouts["111"]["workoutSegments"][0]["workoutSteps"][0][
        "skipLastRestStep"
    ] = True
    config = config_ab(a_exercises=(SQUAT, CALF))

    with caplog.at_level(logging.INFO, logger="repwise.app.update"):
        code = run(rested, config, apply=True)

    assert code == ExitCode.OK
    assert saved_skip(rested.saved, "111") is False
    assert "Restored the last rest on 1 step(s)." in caplog.text


def test_a_dry_run_only_says_the_last_rest_would_come_back(rested, caplog):
    rested.workouts["111"]["workoutSegments"][0]["workoutSteps"][0][
        "skipLastRestStep"
    ] = True
    config = config_ab(a_exercises=(SQUAT, CALF))

    with caplog.at_level(logging.INFO, logger="repwise.app.update"):
        run(rested, config)

    assert rested.saved == []
    assert "1 step(s) would stop skipping their last rest" in caplog.text


# --- step notes -----------------------------------------------------------


@pytest.fixture
def annotated(account):
    """Workout A shaped exactly as the config asks, its note included.

    What a run finds when nothing but workouts.yaml has moved. Activity 700 is
    the session it learns from - 6 reps against a target of 7, so the target
    holds still - which leaves the note as the only thing left to write.
    """
    account.workouts["111"] = steps(
        repeat(rep_step("BARBELL_BACK_SQUAT", "SQUAT", 7, 30.0), sets=SQUAT.sets)
    )
    group = account.workouts["111"]["workoutSegments"][0]["workoutSteps"][0]
    group["workoutSteps"][0]["description"] = SQUAT.note
    return account


def only_workout_a(exercise):
    return Config({"Workout A": Workout("Workout A", "111", ["workout a"], [exercise])})


def test_a_note_the_config_moved_is_reported_beside_the_exercise(annotated, caplog):
    """The report used to say "up to date" against every exercise and count
    the note only in its closing line, which named no exercise at all."""
    with caplog.at_level(logging.INFO, logger="repwise.app.report"):
        run(annotated, only_workout_a(replace(SQUAT, rep_high=12)), activity="700")

    lines = [line for line in caplog.messages if "Barbell Back Squat" in line]
    assert len(lines) == 1, "one exercise, one line"
    assert lines[0].startswith("* Barbell Back Squat")
    assert "note from workouts.yaml" in lines[0]


def test_a_note_is_shown_where_nothing_was_trained(account, caplog):
    """With no session behind it there is no target to hold the columns, so
    the note itself takes them - and an empty before would read as a note that
    says nothing."""
    account.workouts["111"] = steps(
        repeat(rep_step("BARBELL_BACK_SQUAT", "SQUAT", 7, 30.0), sets=SQUAT.sets)
    )
    untrained = Config(
        {"Workout A": Workout("Workout A", "111", ["never trained"], [SQUAT])}
    )

    with caplog.at_level(logging.INFO, logger="repwise.app.report"):
        run(account, untrained)

    assert "no note  ->  6-10 reps | +2.5 kg" in caplog.text


def test_the_summary_still_counts_the_notes(annotated, caplog):
    with caplog.at_level(logging.INFO, logger="repwise.app.update"):
        run(annotated, only_workout_a(replace(SQUAT, rep_high=12)), activity="700")

    assert "1 note(s) would be refreshed" in caplog.text


def test_nothing_is_pushed_without_apply(account):
    run(account)
    assert account.pushed == []


def test_push_without_apply_is_refused():
    """Nothing has been written yet, so there is nothing to send.

    Refused as the options are built, so the run never reaches Garmin.
    """
    with pytest.raises(UsageError, match="only makes sense with --apply"):
        UpdateOptions(push=True)


def test_a_refused_flag_combination_exits_three():
    """The exit code travels with the exception, not with the caller."""
    assert UsageError().exit_code == ExitCode.CONFIG


# --- confirming a push landed ---------------------------------------------


def test_pushing_queues_every_written_workout(account):
    run(account, apply=True, push=True)
    assert sorted(account.pushed) == ["111", "222"]


def test_the_queue_is_read_back_under_verbose(account, caplog):
    """The only way to confirm a push was queued, so -v should show it."""
    with caplog.at_level(logging.DEBUG, logger="repwise.app.update"):
        run(account, apply=True, push=True)

    assert account.queue_reads == 1
    assert "2 message(s) now waiting" in caplog.text


def test_the_queue_is_not_read_on_a_normal_run(account, caplog):
    """It costs a request, and a successful push already says so."""
    with caplog.at_level(logging.INFO, logger="repwise.app.update"):
        run(account, apply=True, push=True)

    assert account.queue_reads == 0


def test_a_push_that_worked_is_not_failed_by_an_unreadable_queue(account, caplog):
    """Reading the queue back is a confirmation, not part of the push."""
    account.queue_failure = GarminError("Could not read the device message queue")

    with caplog.at_level(logging.DEBUG, logger="repwise.app.update"):
        code = run(account, apply=True, push=True)

    assert code == ExitCode.OK
    assert sorted(account.pushed) == ["111", "222"]
    assert "Could not read the device queue back" in caplog.text


# --- reading back how long an exercise had been stalling -------------------
#
# How far a hit moves a target depends on the misses behind it, which live in
# the sessions before this one. Each costs two requests, so the walk goes back
# only as far as one of them is still unsettled.


def executed(*asked):
    """The workout an activity ran, as Garmin keeps it beside the activity.

    Sets are FIT's repeats rather than nesting: a repeat step *after* the run
    it repeats, naming the step to jump back to and how many times.
    """
    steps_out, index = [], 0
    for name, category, reps, sets in asked:
        start = index
        steps_out.append(
            {
                "stepIndex": index,
                "intensity": "ACTIVE",
                "durationType": "REPS",
                "durationValue": float(reps),
                "exerciseName": name,
                "exerciseCategory": category,
            }
        )
        index += 1
        steps_out.append(
            {
                "stepIndex": index,
                "intensity": None,
                "durationType": "REPEAT_UNTIL_STEPS_CMPLT",
                "durationValue": float(start),
                "targetValue": float(sets),
            }
        )
        index += 1
    return [{"workoutName": "Workout A", "steps": steps_out}]


def squats(reps, sets=3, weight=30000.0):
    return [active("BARBELL_BACK_SQUAT", "SQUAT", reps, weight)] * sets


@pytest.fixture
def stalling():
    """Workout A squatted 8,8,8 against a target of 8, twice missing 8 before."""
    return FakeSession(
        activities=[an_activity(n, "Workout A") for n in (900, 800, 700, 600, 500)],
        workouts={
            "111": steps(repeat(rep_step("BARBELL_BACK_SQUAT", "SQUAT", 8, 30.0)))
        },
        sets={
            "900": sets_of(*squats(8)),
            "800": sets_of(*squats(7)),
            "700": sets_of(*squats(7)),
            "600": sets_of(*squats(6)),
            "500": sets_of(*squats(6)),
        },
        executed={
            str(n): executed(("BARBELL_BACK_SQUAT", "SQUAT", 8, 3))
            for n in (900, 800, 700, 600, 500)
        },
    )


def only_a():
    return Config({"Workout A": Workout("Workout A", "111", ["workout a"], [SQUAT])})


def test_the_sessions_before_this_one_are_the_older_ones(stalling):
    earlier = sessions_before(stalling.activities, only_a()["Workout A"], "800")
    assert [a["activityId"] for a in earlier] == [700, 600, 500]


def test_an_activity_too_old_to_appear_leaves_no_history(stalling):
    assert sessions_before(stalling.activities, only_a()["Workout A"], "1") == []


def test_a_stall_is_read_back_as_far_as_it_goes(stalling):
    workout = only_a()["Workout A"]
    earlier = sessions_before(stalling.activities, workout, "900")
    history = gather_history(
        stalling, workout, earlier, performed_sets(stalling.sets["900"])
    )

    assert [s.performed[0].reps for s in history["barbellbacksquat"]] == [7, 7]
    assert stalling.read_back == ["800", "700"], "stopped at sets - 1 misses"


def test_a_smooth_session_reads_back_one_activity_and_stops(stalling):
    """The session before this one hit, so nothing deeper can change anything."""
    stalling.sets["800"] = sets_of(*squats(8))
    workout = only_a()["Workout A"]
    earlier = sessions_before(stalling.activities, workout, "900")
    gather_history(stalling, workout, earlier, performed_sets(stalling.sets["900"]))

    assert stalling.read_back == ["800"]


def test_a_change_of_load_ends_the_walk(stalling):
    """A different weight is a different ladder, so its misses do not count."""
    stalling.sets["800"] = sets_of(*squats(7, weight=27500.0))
    workout = only_a()["Workout A"]
    earlier = sessions_before(stalling.activities, workout, "900")
    gather_history(stalling, workout, earlier, performed_sets(stalling.sets["900"]))

    assert stalling.read_back == ["800"]


def test_a_stall_shortens_the_advance_of_the_session_that_ends_it(stalling):
    """8,8,8 after two misses earns one set, not three: 9,8,8 rather than 9,9,9."""
    run_update(stalling, only_a(), args(apply=True))

    written = stalling.saved[0][1]["workoutSegments"][0]["workoutSteps"]
    asked = [
        (group["numberOfIterations"], group["workoutSteps"][0]["endConditionValue"])
        for group in written
        if group.get("workoutSteps")
    ]
    assert asked == [(1, 9.0), (2, 8.0)]


def test_without_a_stall_behind_it_the_whole_target_still_moves(stalling):
    """The rule this replaces is its own streak-free case, and must be intact."""
    stalling.sets["800"] = sets_of(*squats(8))
    run_update(stalling, only_a(), args(apply=True))

    written = stalling.saved[0][1]["workoutSegments"][0]["workoutSteps"]
    asked = [
        (group["numberOfIterations"], group["workoutSteps"][0]["endConditionValue"])
        for group in written
        if group.get("workoutSteps")
    ]
    assert asked == [(3, 9.0)], "one group of three at nine, as before"


def test_an_account_with_no_executed_record_progresses_as_it_always_did(stalling):
    """Nothing to read back is no stall, not a failure."""
    stalling.executed = {}
    run_update(stalling, only_a(), args(apply=True))

    written = stalling.saved[0][1]["workoutSegments"][0]["workoutSteps"]
    asked = [
        group["workoutSteps"][0]["endConditionValue"]
        for group in written
        if group.get("workoutSteps")
    ]
    assert asked == [9.0]


# --- running twice ---------------------------------------------------------


def test_running_twice_writes_nothing_the_second_time(stalling):
    """The same activity is still the latest one after --apply."""
    run_update(stalling, only_a(), args(apply=True))
    assert stalling.saved, "the first run had something to write"

    stalling.saved.clear()
    run_update(stalling, only_a(), args(apply=True))
    assert stalling.saved == []


def test_a_second_run_does_not_deload_what_the_first_one_earned(stalling):
    """Judged twice, the session reads as a miss and then as a stall."""
    run_update(stalling, only_a(), args(apply=True))
    after_one = [
        (g["numberOfIterations"], g["workoutSteps"][0]["endConditionValue"])
        for g in stalling.workouts["111"]["workoutSegments"][0]["workoutSteps"]
        if g.get("workoutSteps")
    ]

    run_update(stalling, only_a(), args(apply=True))
    after_two = [
        (g["numberOfIterations"], g["workoutSteps"][0]["endConditionValue"])
        for g in stalling.workouts["111"]["workoutSegments"][0]["workoutSteps"]
        if g.get("workoutSteps")
    ]

    assert after_one == [(1, 9.0), (2, 8.0)], "eased up by one set"
    assert after_two == after_one, "and stayed there"
