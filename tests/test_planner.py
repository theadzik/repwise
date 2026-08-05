"""Matching workout steps to exercises, and planning the updates."""

from dataclasses import replace

import pytest
from builders import active, rep_step, repeat, rest_step, spec, workout

from workout.domain.models import Config, Workout
from workout.domain.progression import Target
from workout.garmin.payloads import (
    iter_exercise_blocks,
    performed_sets,
    step_note,
    step_target,
)
from workout.planner import (
    ActivityNotFound,
    decided_targets,
    find_workout,
    index_specs,
    plan_sync,
    plan_workout,
)

SQUAT = spec(sets=3, weight_step=2.5)
CURLS = spec(
    name="Standing Alternating Dumbbell Curls",
    garmin_name="STANDING_ALTERNATING_DUMBBELL_CURLS",
    garmin_category="CURL",
    rep_low=10,
    rep_high=15,
    sets=2,
    load="dumbbell",
    weight_step=1.0,
)
LATERAL = spec(
    name="Dumbbell Lateral Raise",
    garmin_name="DUMBBELL_LATERAL_RAISE",
    garmin_category="LATERAL_RAISE",
    rep_low=12,
    rep_high=15,
    sets=3,
    load="dumbbell",
    weight_step=1.0,
)
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


def a_workout(key="Workout A", wid="1", exercises=(SQUAT,)):
    return Workout(key, wid, ["trening a"], list(exercises))


# --- routing --------------------------------------------------------------


def test_find_workout_matches_a_prefix():
    """Prefixes are arbitrary strings, so non-English names work too."""
    config = Config({"Workout A": a_workout()})
    assert find_workout(config, "Trening A - evening").key == "Workout A"


def test_find_workout_rejects_an_unknown_name():
    config = Config({"Workout A": a_workout()})
    with pytest.raises(ActivityNotFound):
        find_workout(config, "Morning Run")


# --- indexing -------------------------------------------------------------


def test_category_index_skips_ambiguous_categories():
    """Two exercises sharing a category cannot be told apart by it."""
    other = spec(name="Front Squat", garmin_name="FRONT_SQUAT", garmin_category="SQUAT")
    assert index_specs([SQUAT, other]).by_category("SQUAT") is None


def test_category_index_keeps_unique_categories():
    assert index_specs([SQUAT, CURLS]).by_category("CURL") is CURLS


def test_the_friendly_name_is_an_alias_for_the_garmin_one():
    """A step named either way finds the same spec."""
    index = index_specs([SQUAT])
    assert index.by_name("BARBELL_BACK_SQUAT") is SQUAT
    assert index.by_name("Barbell Back Squat") is SQUAT


# --- planning -------------------------------------------------------------


def test_plan_advances_a_met_target():
    payload = workout(rep_step("BARBELL_BACK_SQUAT", "SQUAT", 7, 20.0))
    performed = performed_sets(
        {"exerciseSets": [active("BARBELL_BACK_SQUAT", "SQUAT", 7, 20000.0)] * 3}
    )
    plan = plan_workout(a_workout(), payload, performed)

    assert [c.new for c in plan.changes] == [Target(8, 20.0)]
    assert plan.moved, "the step changed"
    # The payload itself is updated, ready to be written back.
    steps = payload["workoutSegments"][0]["workoutSteps"]
    assert step_target(next(iter(steps))) == Target(8, 20.0)


def test_plan_matches_by_category_when_the_logged_name_differs():
    """Garmin logs SEATED_... where the workout programs STANDING_..."""
    payload = workout(rep_step("STANDING_ALTERNATING_DUMBBELL_CURLS", "CURL", 10, 7.0))
    performed = performed_sets(
        {
            "exerciseSets": [active("SEATED_DUMBBELL_BICEPS_CURL", "CURL", 10, 7000.0)]
            * 2
        }
    )
    plan = plan_workout(a_workout(exercises=[CURLS]), payload, performed)

    assert not plan.warnings, "the category bridged the differing names"
    assert plan.changes[0].new == Target(11, 7.0)


def test_plan_warns_about_an_unknown_exercise():
    payload = workout(rep_step("MYSTERY_LIFT", "MYSTERY", 5, 1.0))
    plan = plan_workout(a_workout(), payload, ({}, {}))
    assert plan.changes == []
    assert "not in workouts.yaml" in plan.warnings[0]


def test_plan_warns_when_an_exercise_was_not_performed():
    payload = workout(rep_step("BARBELL_BACK_SQUAT", "SQUAT", 7, 20.0))
    plan = plan_workout(a_workout(), payload, ({}, {}))
    assert plan.changes == []
    assert "not found in the activity" in plan.warnings[0]


def test_plan_leaves_an_unchanged_step_alone():
    payload = workout(rep_step("BARBELL_BACK_SQUAT", "SQUAT", 8, 20.0))
    performed = performed_sets(
        {"exerciseSets": [active("BARBELL_BACK_SQUAT", "SQUAT", 6, 20000.0)] * 3}
    )
    plan = plan_workout(a_workout(), payload, performed)
    assert plan.changes and not plan.moved, "missed target, so nothing moves"


def test_plan_does_not_write_back_a_load_below_the_range():
    """A heavier session short of rep_low must leave the payload untouched."""
    payload = workout(rep_step("DUMBBELL_LATERAL_RAISE", "LATERAL_RAISE", 13, 3.0))
    performed = performed_sets(
        {
            "exerciseSets": [
                active("DUMBBELL_LATERAL_RAISE", "LATERAL_RAISE", 8, 4000.0)
            ]
            * 3
        }
    )
    plan = plan_workout(a_workout(exercises=[LATERAL]), payload, performed)

    assert not plan.moved, "the 4 kg was not earned"
    steps = payload["workoutSegments"][0]["workoutSteps"]
    assert step_target(next(iter(steps))) == Target(13, 3.0), "still 13 x 3 kg"


# --- notes ----------------------------------------------------------------


def test_plan_writes_the_programming_into_the_step_note():
    payload = workout(rep_step("BARBELL_BACK_SQUAT", "SQUAT", 7, 20.0))
    performed = performed_sets(
        {"exerciseSets": [active("BARBELL_BACK_SQUAT", "SQUAT", 7, 20000.0)] * 3}
    )
    plan = plan_workout(a_workout(), payload, performed)

    step = next(iter(payload["workoutSegments"][0]["workoutSteps"]))
    assert step_note(step) == "6-10 reps | +2.5 kg"
    assert plan.notes == ["Barbell Back Squat"]


def test_note_is_written_even_when_the_exercise_was_not_performed():
    """The note describes the programming, not the session, so a skipped
    exercise still gets one."""
    payload = workout(rep_step("BARBELL_BACK_SQUAT", "SQUAT", 7, 20.0))
    plan = plan_workout(a_workout(), payload, ({}, {}))

    assert plan.changes == [], "nothing was logged, so no target moved"
    assert plan.notes == ["Barbell Back Squat"]
    assert plan.writable, "a note alone is worth writing"


def test_an_up_to_date_note_is_left_alone():
    """Idempotent: a second run must not re-save the workout for nothing."""
    step = rep_step("BARBELL_BACK_SQUAT", "SQUAT", 7, 20.0)
    step["description"] = SQUAT.note
    plan = plan_workout(a_workout(), workout(step), ({}, {}))

    assert plan.notes == []
    assert not plan.writable


def test_a_stale_generated_note_is_replaced():
    """Editing workouts.yaml is the whole reason this has to be rewritable."""
    widened = replace(LATERAL, rep_high=20)
    step = rep_step("DUMBBELL_LATERAL_RAISE", "LATERAL_RAISE", 13, 3.0)
    step["description"] = LATERAL.note  # "12-15 reps | +1 kg", before widening
    plan = plan_workout(a_workout(exercises=[widened]), workout(step), ({}, {}))

    assert step_note(step) == "12-20 reps | +1 kg"
    assert plan.notes == ["Dumbbell Lateral Raise"]


def test_a_hand_written_note_is_never_overwritten():
    """Losing a coaching cue you typed into Connect would be silent, so the
    tool reports it and keeps its hands off."""
    step = rep_step("BARBELL_BACK_SQUAT", "SQUAT", 7, 20.0)
    step["description"] = "knees out, brace before the descent"
    plan = plan_workout(a_workout(), workout(step), ({}, {}))

    assert step_note(step) == "knees out, brace before the descent"
    assert plan.notes == []
    assert any("has its own note" in w for w in plan.warnings)


def test_notes_reach_a_workout_that_only_receives_a_sync():
    payload = workout(rep_step("WEIGHTED_STANDING_CALF_RAISE", "CALF_RAISE", 12, 0.0))
    targets = {"weightedstandingcalfraise": Target(12, 20.0)}

    plan = plan_sync(a_workout("Workout B", "2", [CALF]), payload, targets, "Workout A")
    assert plan.notes == ["Weighted Standing Calf Raise"]


# --- rest between sets ----------------------------------------------------


RESTED = spec(sets=3, weight_step=2.5, rest=150)


def squat_group(rest=120.0, reps=7):
    """The squat as Garmin stores it: a repeat group with a timed rest."""
    step = rep_step("BARBELL_BACK_SQUAT", "SQUAT", reps, 20.0)
    return workout(repeat(step, sets=3, rest=rest))


def rest_of(built):
    return next(iter(iter_exercise_blocks(built))).rest


def test_a_configured_rest_is_written_onto_the_rest_step():
    """workouts.yaml is the source of the programming, so it wins."""
    built = squat_group(rest=120.0)
    plan = plan_workout(a_workout(exercises=[RESTED]), built, ({}, {}))

    assert rest_of(built) == 150
    assert [(c.old, c.new) for c in plan.rests] == [(120, 150)]
    assert plan.writable, "a rest alone is worth writing"


def test_an_already_correct_rest_is_left_alone():
    """Idempotent: a second run must not re-save the workout for nothing."""
    step = rep_step("BARBELL_BACK_SQUAT", "SQUAT", 7, 20.0)
    step["description"] = RESTED.note  # so only the rest could be a reason
    built = workout(repeat(step, sets=3, rest=150.0))

    plan = plan_workout(a_workout(exercises=[RESTED]), built, ({}, {}))

    assert plan.rests == []
    assert not plan.writable


def test_an_exercise_with_no_rest_configured_keeps_garmins():
    """Leaving `rest` out is having no opinion, not asking for zero."""
    built = squat_group(rest=120.0)
    plan = plan_workout(a_workout(exercises=[SQUAT]), built, ({}, {}))

    assert plan.rests == []
    assert rest_of(built) == 120


def test_a_lap_button_rest_is_reported_rather_than_retimed():
    """Turning a button press into a countdown changes how it is performed."""
    button = rest_step()
    group = {
        "type": "RepeatGroupDTO",
        "numberOfIterations": 3,
        "workoutSteps": [rep_step("BARBELL_BACK_SQUAT", "SQUAT", 7, 20.0), button],
    }
    plan = plan_workout(a_workout(exercises=[RESTED]), workout(group), ({}, {}))

    assert plan.rests == []
    assert button["endCondition"] == {"conditionTypeKey": "lap.button"}
    assert any("not a fixed time in Garmin" in w for w in plan.warnings)


def test_a_rest_of_zero_is_written_rather_than_called_unwritable():
    """Zero is what the step says today, not a step that cannot hold a rest."""
    built = squat_group(rest=0.0)
    plan = plan_workout(a_workout(exercises=[RESTED]), built, ({}, {}))

    assert [(c.old, c.new) for c in plan.rests] == [(0, 150)]
    assert rest_of(built) == 150
    assert not [w for w in plan.warnings if "rest" in w]


def test_the_rest_moves_even_when_the_target_does_not():
    """The rest describes the programming, not the session."""
    built = squat_group(rest=120.0, reps=7)
    performed = performed_sets(
        {"exerciseSets": [active("BARBELL_BACK_SQUAT", "SQUAT", 5, 20000.0)] * 3}
    )
    plan = plan_workout(a_workout(exercises=[RESTED]), built, performed)

    assert not plan.moved, "missed the target"
    assert rest_of(built) == 150


def test_rests_reach_a_workout_that_only_receives_a_sync():
    step = rep_step("WEIGHTED_STANDING_CALF_RAISE", "CALF_RAISE", 12, 0.0)
    built = workout(repeat(step, sets=3, rest=60.0))
    rested_calf = replace(CALF, rest=90)
    targets = {"weightedstandingcalfraise": Target(12, 20.0)}

    plan = plan_sync(
        a_workout("Workout B", "2", [rested_calf]), built, targets, "Workout A"
    )

    assert [(c.old, c.new) for c in plan.rests] == [(60, 90)]
    assert rest_of(built) == 90


# --- syncing shared exercises ---------------------------------------------


def test_sync_pushes_a_decided_target_into_another_workout():
    payload = workout(rep_step("WEIGHTED_STANDING_CALF_RAISE", "CALF_RAISE", 12, 0.0))
    targets = {"weightedstandingcalfraise": Target(12, 20.0)}

    plan = plan_sync(a_workout("Workout B", "2", [CALF]), payload, targets, "Workout A")

    assert plan.moved
    assert plan.changes[0].new == Target(12, 20.0)
    assert "synced from Workout A" in plan.changes[0].reason


def test_sync_ignores_exercises_that_did_not_move():
    payload = workout(rep_step("BARBELL_BACK_SQUAT", "SQUAT", 7, 20.0))
    plan = plan_sync(a_workout(), payload, {"somethingelse": Target(9, 9.0)}, "A")
    assert plan.changes == []


def test_sync_warns_when_a_target_leaves_the_range():
    payload = workout(rep_step("WEIGHTED_STANDING_CALF_RAISE", "CALF_RAISE", 12, 0.0))
    targets = {"weightedstandingcalfraise": Target(30, 20.0)}  # above rep_high 20

    plan = plan_sync(a_workout("Workout B", "2", [CALF]), payload, targets, "Workout A")
    assert "outside this workout's 12-20 range" in plan.warnings[0]


def test_decided_targets_only_reports_what_moved():
    payload = workout(
        rep_step("BARBELL_BACK_SQUAT", "SQUAT", 7, 20.0),
        rep_step("STANDING_ALTERNATING_DUMBBELL_CURLS", "CURL", 10, 7.0),
    )
    performed = performed_sets(
        {
            "exerciseSets": [active("BARBELL_BACK_SQUAT", "SQUAT", 7, 20000.0)] * 3
            + [active("STANDING_ALTERNATING_DUMBBELL_CURLS", "CURL", 5, 7000.0)] * 2
        }
    )
    plan = plan_workout(a_workout(exercises=[SQUAT, CURLS]), payload, performed)

    # The squat advanced; the curls missed their target and did not.
    assert set(decided_targets(plan)) == {"barbellbacksquat"}
