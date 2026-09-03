"""Matching workout steps to exercises, and planning the updates."""

from copy import deepcopy
from dataclasses import replace

import pytest
from builders import (
    active,
    payload,
    rep_step,
    repeat,
    rest_step,
    spec,
    timed_rest,
    workout,
)

from repwise.domain.models import Config, Workout
from repwise.domain.progression import Target
from repwise.garmin.payloads import (
    is_timed_rest,
    iter_exercise_blocks,
    performed_sets,
    step_note,
    step_target,
)
from repwise.planner import (
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
    payload = workout(
        repeat(
            rep_step("STANDING_ALTERNATING_DUMBBELL_CURLS", "CURL", 10, 7.0),
            sets=CURLS.sets,
        )
    )
    performed = performed_sets(
        {
            "exerciseSets": [active("SEATED_DUMBBELL_BICEPS_CURL", "CURL", 10, 7000.0)]
            * 2
        }
    )
    plan = plan_workout(a_workout(exercises=[CURLS]), payload, performed)

    assert not plan.warnings, "the category bridged the differing names"
    assert plan.changes[0].new == Target(11, 7.0)


# --- a movement Garmin promoted to its loaded variant ---------------------
#
# Garmin renames a set the moment a weight goes on it: a workout programming
# SEATED_CALF_RAISE comes back holding WEIGHTED_SEATED_CALF_RAISE. Both the
# programmed exercise and the one beside it sharing its category are here,
# which is the shape that made the rename cost something.

HOME_SEATED_CALF = spec(
    name="Seated Calf Raise",
    garmin_name="SEATED_CALF_RAISE",
    garmin_category="CALF_RAISE",
    rep_low=12,
    rep_high=18,
    sets=3,
    load="dumbbell",
    weight_step=2.5,
)
HOME_SINGLE_LEG_CALF = spec(
    name="Single-leg Standing Calf Raise",
    garmin_name="SINGLE_LEG_STANDING_CALF_RAISE",
    garmin_category="CALF_RAISE",
    rep_low=12,
    rep_high=20,
    sets=4,
    load="dumbbell",
    weight_step=2.5,
)


def a_home_calf_workout():
    """Both calf exercises, as the workout holds them."""
    return workout(
        repeat(rep_step("SEATED_CALF_RAISE", "CALF_RAISE", 12, 0.0), sets=3),
        repeat(
            rep_step("SINGLE_LEG_STANDING_CALF_RAISE", "CALF_RAISE", 12, 0.0), sets=4
        ),
    )


def test_the_loaded_variant_is_an_alias_for_the_plain_name():
    """Both directions: a weight added on the watch, or taken off."""
    index = index_specs([HOME_SEATED_CALF, CALF])
    assert index.by_name("WEIGHTED_SEATED_CALF_RAISE") is HOME_SEATED_CALF
    assert index.by_name("STANDING_CALF_RAISE") is CALF


def test_plan_reads_sets_logged_under_the_loaded_variant():
    """20 kg on a movement programmed unloaded is still that movement."""
    performed = performed_sets(
        {
            "exerciseSets": [
                active("WEIGHTED_SEATED_CALF_RAISE", "CALF_RAISE", 16, 20000.0)
            ]
            * 3
            + [active("SINGLE_LEG_STANDING_CALF_RAISE", "CALF_RAISE", 12, 0.0)] * 4
        }
    )
    plan = plan_workout(
        a_workout(exercises=[HOME_SEATED_CALF, HOME_SINGLE_LEG_CALF]),
        a_home_calf_workout(),
        performed,
    )

    seated = next(c for c in plan.changes if c.spec is HOME_SEATED_CALF)
    assert seated.new == Target(17, 20.0), "its own sets, at the load they were done at"


def test_an_ambiguous_category_is_not_a_pile_of_sets_to_progress_from():
    """Two exercises share CALF_RAISE, so it cannot say which was trained.

    Answering with every calf set in the session is worse than answering with
    none: merged, the two decide a working weight and a rep floor between them
    by set count alone, and the target that comes out is neither exercise's.
    """
    performed = performed_sets(
        {
            "exerciseSets": [active("MYSTERY_CALF_MACHINE", "CALF_RAISE", 9, 5000.0)]
            * 3
            + [active("SINGLE_LEG_STANDING_CALF_RAISE", "CALF_RAISE", 12, 0.0)] * 4
        }
    )
    plan = plan_workout(
        a_workout(exercises=[HOME_SEATED_CALF, HOME_SINGLE_LEG_CALF]),
        a_home_calf_workout(),
        performed,
    )

    assert [c.spec for c in plan.changes] == [HOME_SINGLE_LEG_CALF], (
        "only the one matched"
    )
    assert "Seated Calf Raise: not found in the activity" in plan.warnings[0]


def test_plan_removes_an_exercise_the_config_does_not_name():
    """It used to be warned about and left alone. The config drives now, so
    what it stops naming stops being in the workout."""
    payload = workout(rep_step("MYSTERY_LIFT", "MYSTERY", 5, 1.0))
    plan = plan_workout(a_workout(), payload, ({}, {}))

    assert ("removed", "MYSTERY_LIFT") in [(c.kind, c.name) for c in plan.structure]
    assert plan.changes == []


def test_plan_warns_when_an_exercise_was_not_performed():
    payload = workout(repeat(rep_step("BARBELL_BACK_SQUAT", "SQUAT", 7, 20.0), sets=3))
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


def noted(plan):
    """Which notes moved, and what they said either side of the move."""
    return [(change.spec.name, change.old, change.new) for change in plan.notes]


def test_plan_writes_the_programming_into_the_step_note():
    payload = workout(rep_step("BARBELL_BACK_SQUAT", "SQUAT", 7, 20.0))
    performed = performed_sets(
        {"exerciseSets": [active("BARBELL_BACK_SQUAT", "SQUAT", 7, 20000.0)] * 3}
    )
    plan = plan_workout(a_workout(), payload, performed)

    step = next(iter(payload["workoutSegments"][0]["workoutSteps"]))
    assert step_note(step) == "6-10 reps | +2.5 kg"
    assert noted(plan) == [("Barbell Back Squat", "", "6-10 reps | +2.5 kg")]


def test_note_is_written_even_when_the_exercise_was_not_performed():
    """The note describes the programming, not the session, so a skipped
    exercise still gets one."""
    payload = workout(rep_step("BARBELL_BACK_SQUAT", "SQUAT", 7, 20.0))
    plan = plan_workout(a_workout(), payload, ({}, {}))

    assert plan.changes == [], "nothing was logged, so no target moved"
    assert noted(plan) == [("Barbell Back Squat", "", "6-10 reps | +2.5 kg")]
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
    assert noted(plan) == [
        ("Dumbbell Lateral Raise", "12-15 reps | +1 kg", "12-20 reps | +1 kg")
    ], "the report has both halves of the edit, not just the exercise's name"


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
    assert noted(plan) == [("Weighted Standing Calf Raise", "", "12-20 reps | +5 kg")]


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


# --- the rest after the last set ------------------------------------------
#
# Connect can drop it per repeat group, which leaves one exercise resting after
# every set but its last. The config gives an exercise one rest and means all
# of them, so a group set to skip is put back.


def skipping(built, skip=True):
    """Turn Connect's switch on for every repeat group in a workout."""
    for group in built["workoutSegments"][0]["workoutSteps"]:
        if group["type"] == "RepeatGroupDTO":
            group["skipLastRestStep"] = skip
    return built


def skips_of(built):
    return [
        group.get("skipLastRestStep")
        for group in built["workoutSegments"][0]["workoutSteps"]
        if group["type"] == "RepeatGroupDTO"
    ]


def test_a_group_skipping_its_last_rest_is_put_back():
    built = skipping(squat_group(rest=150.0))
    plan = plan_workout(a_workout(exercises=[RESTED]), built, ({}, {}))

    assert skips_of(built) == [False]
    assert [c.spec.name for c in plan.skips] == [RESTED.name]
    assert plan.writable, "the last rest alone is worth writing"


def test_the_last_rest_is_restored_without_a_configured_rest():
    """`rest` says how long; whether the last set gets one is not its call."""
    built = skipping(squat_group(rest=120.0))
    plan = plan_workout(a_workout(exercises=[SQUAT]), built, ({}, {}))

    assert skips_of(built) == [False]
    assert [c.spec.name for c in plan.skips] == [SQUAT.name]
    assert plan.rests == [], "the interval itself was not the config's to move"


@pytest.mark.parametrize("stored", [False, None])
def test_a_group_that_already_rests_is_left_alone(stored):
    """Idempotent, and null reads as not skipping, which is how Garmin has it."""
    step = rep_step("BARBELL_BACK_SQUAT", "SQUAT", 7, 20.0)
    step["description"] = RESTED.note  # so only the last rest could be a reason
    built = skipping(workout(repeat(step, sets=3, rest=150.0)), skip=stored)

    plan = plan_workout(a_workout(exercises=[RESTED]), built, ({}, {}))

    assert plan.skips == []
    assert not plan.writable


def test_both_halves_of_a_ramp_stop_skipping_but_count_once():
    """Two groups, one exercise: it rests the same after every set of it."""
    ramped = replace(RESTED, sets=4)
    built = skipping(
        payload(
            repeat(rep_step("BARBELL_BACK_SQUAT", "SQUAT", 8, 20.0), 2, 150.0),
            repeat(rep_step("BARBELL_BACK_SQUAT", "SQUAT", 7, 20.0), 2, 150.0),
        )
    )

    plan = plan_workout(a_workout(exercises=[ramped]), built, ({}, {}))

    assert skips_of(built) == [False, False]
    assert [c.spec.name for c in plan.skips] == [ramped.name]


def test_the_last_rest_is_restored_in_a_workout_that_only_receives_a_sync():
    step = rep_step("WEIGHTED_STANDING_CALF_RAISE", "CALF_RAISE", 12, 0.0)
    built = skipping(workout(repeat(step, sets=3, rest=60.0)))
    targets = {"weightedstandingcalfraise": Target(12, 20.0)}

    plan = plan_sync(a_workout("Workout B", "2", [CALF]), built, targets, "Workout A")

    assert skips_of(built) == [False]
    assert [c.spec.name for c in plan.skips] == [CALF.name]


# --- the shape of the workout ---------------------------------------------
#
# The config decides which exercises a workout holds and in what order. Garmin
# keeps where each one has got to, so a step it already has is moved rather
# than rebuilt - rebuilding would quietly restart the progression stored in it.


def group_of(spec, reps, weight, sets=None, rest=90.0):
    """An exercise as Garmin really stores one: wrapped in a repeat group.

    The set count follows the spec unless a test is about them disagreeing.
    """
    return repeat(
        rep_step(spec.garmin_name, spec.garmin_category, reps, weight),
        spec.sets if sets is None else sets,
        rest,
    )


def as_built(payload):
    """The exercises in the order the workout now performs them."""
    return [b.step["exerciseName"] for b in iter_exercise_blocks(payload)]


def test_an_exercise_the_config_adds_is_built_at_the_bottom_of_its_range():
    built = payload(group_of(SQUAT, 7, 20.0))
    config = a_workout(exercises=[SQUAT, replace(CURLS, start_weight=6.0)])

    plan = plan_workout(config, built, ({}, {}))

    assert as_built(built) == [
        "BARBELL_BACK_SQUAT",
        "STANDING_ALTERNATING_DUMBBELL_CURLS",
    ]
    added = [c for c in plan.structure if c.kind == "added"]
    assert [(c.name, c.position) for c in added] == [
        ("Standing Alternating Dumbbell Curls", 2)
    ]
    assert added[0].target == Target(CURLS.rep_low, 6.0)


def test_the_config_order_becomes_the_workouts_order():
    built = payload(group_of(SQUAT, 7, 20.0), group_of(CURLS, 10, 7.0))
    config = a_workout(exercises=[CURLS, SQUAT])

    plan = plan_workout(config, built, ({}, {}))

    assert as_built(built) == [
        "STANDING_ALTERNATING_DUMBBELL_CURLS",
        "BARBELL_BACK_SQUAT",
    ]
    assert [(c.kind, c.name, c.position) for c in plan.structure] == [
        ("moved", "Barbell Back Squat", 2)
    ], "one move explains it, reported under the name the config gives it"


def test_moving_one_exercise_is_reported_as_one_move():
    """The last exercise brought to the front is one move. Everything else
    shifting down is a consequence, not nine more things to read about."""
    built = payload(
        group_of(SQUAT, 7, 20.0), group_of(CURLS, 10, 7.0), group_of(CALF, 15, 20.0)
    )
    config = a_workout(exercises=[CALF, SQUAT, CURLS])

    plan = plan_workout(config, built, ({}, {}))

    assert as_built(built)[0] == "WEIGHTED_STANDING_CALF_RAISE"
    assert [(c.kind, c.name) for c in plan.structure] == [
        ("moved", "Weighted Standing Calf Raise")
    ]


def test_a_swap_reports_the_two_that_swapped_and_not_the_one_between():
    """Either exercise of a swap, or the one they cross, explains the new order
    in two moves. It goes to the two that are no longer where they were."""
    built = payload(
        group_of(SQUAT, 7, 20.0),
        group_of(CURLS, 10, 7.0),
        group_of(LATERAL, 12, 5.0),
        group_of(CALF, 15, 20.0),
    )
    config = a_workout(exercises=[SQUAT, CALF, LATERAL, CURLS])

    plan = plan_workout(config, built, ({}, {}))

    assert [(c.name, c.position) for c in plan.structure] == [
        ("Weighted Standing Calf Raise", 2),
        ("Standing Alternating Dumbbell Curls", 4),
    ], "the lateral raise never moved, so it is not one of the two"


def test_a_moved_exercise_keeps_the_target_it_had():
    """The whole reason for moving the step rather than building a new one."""
    built = payload(group_of(SQUAT, 7, 20.0), group_of(CURLS, 13, 7.0))

    plan_workout(a_workout(exercises=[CURLS, SQUAT]), built, ({}, {}))

    first, second = iter_exercise_blocks(built)
    assert step_target(first.step) == Target(13, 7.0), "not reset to rep_low"
    assert step_target(second.step) == Target(7, 20.0)
    assert (first.sets, first.rest) == (CURLS.sets, 90), "its sets and rest travelled"


def test_inserting_at_the_top_does_not_report_everything_below_as_moved():
    """Position shifts because something was added, which is not a move."""
    built = payload(group_of(SQUAT, 7, 20.0), group_of(CURLS, 10, 7.0))
    config = a_workout(exercises=[LATERAL, SQUAT, CURLS])

    plan = plan_workout(config, built, ({}, {}))

    assert [c.kind for c in plan.structure] == ["added"]


def test_an_unchanged_workout_is_left_exactly_as_it_was():
    """The idempotency that stops a run writing for the sake of it. Verified
    against a real account for the payload half; this is the planner half."""
    built = payload(group_of(SQUAT, 7, 20.0), group_of(CURLS, 10, 7.0))
    groups = built["workoutSegments"][0]["workoutSteps"]
    for group, each in zip(groups, [SQUAT, CURLS], strict=True):
        group["workoutSteps"][0]["description"] = each.note
    before = deepcopy(built)

    plan = plan_workout(a_workout(exercises=[SQUAT, CURLS]), built, ({}, {}))

    assert plan.structure == []
    assert not plan.writable
    assert built == before, "not one field touched"


def test_a_newly_built_exercise_is_not_reported_as_missing_from_the_activity():
    """It was created a moment ago by this same run; of course it was not
    performed. Saying so would be noise around the line that matters."""
    built = payload(group_of(SQUAT, 7, 20.0))
    performed = performed_sets(
        {"exerciseSets": [active("BARBELL_BACK_SQUAT", "SQUAT", 7, 20000.0)] * 3}
    )

    plan = plan_workout(a_workout(exercises=[SQUAT, CURLS]), built, performed)

    assert [c.kind for c in plan.structure] == ["added"]
    assert not [w for w in plan.warnings if "not found in the activity" in w]


def test_the_rests_between_exercises_are_kept_as_they_were():
    """Reordering must not quietly retime the gaps, which Phase 5 owns."""
    built = payload(group_of(SQUAT, 7, 20.0), rest_step(60.0), group_of(CURLS, 10, 7.0))

    plan_workout(a_workout(exercises=[CURLS, SQUAT]), built, ({}, {}))

    steps = built["workoutSegments"][0]["workoutSteps"]
    assert steps[1]["endCondition"]["conditionTypeKey"] == "lap.button"
    assert steps[1]["endConditionValue"] == 60.0, "the value Garmin stored, untouched"


def test_a_workout_with_no_session_behind_it_plans_no_targets():
    """What Phase 4 needs: shape the workout without pretending it was trained."""
    built = payload(group_of(SQUAT, 7, 20.0))

    plan = plan_workout(a_workout(exercises=[SQUAT, CURLS]), built)

    assert plan.changes == [], "no session, so nothing earned"
    assert plan.warnings == [], "and nothing to complain about"
    assert [c.kind for c in plan.structure] == ["added"]
    assert plan.notes, "but the programming still reaches the steps"


# --- a removal that is probably a typo -------------------------------------
#
# Removing one exercise and building another is what a mistyped garmin_name
# looks like from inside the planner, and the target lives in the step being
# dropped. The warning goes where the damage would be done - in the plan you
# are about to apply - rather than in a command you may not have run.


def test_a_rename_caught_by_the_shared_category_is_warned_about():
    """A category rescues a mistyped name while only one exercise claims it.
    Add a second squat and it cannot, and the typo turns destructive."""
    front = spec(name="Front Squat", garmin_name="FRONT_SQUAT", garmin_category="SQUAT")
    built = payload(group_of(SQUAT, 7, 20.0), group_of(front, 8, 15.0))
    typo = replace(SQUAT, garmin_name="BARBELL_BACK_SQUATT")

    plan = plan_workout(a_workout(exercises=[typo, front]), built)

    assert {c.kind for c in plan.structure} == {"added", "removed"}
    assert any("its target is lost" in w for w in plan.warnings)


def test_a_rename_caught_by_the_name_alone_is_warned_about():
    """No category to bridge them, but one name contains the other."""
    plain = replace(CALF, garmin_name="LEG_CURL", garmin_category=None)
    weighted = replace(plain, garmin_name="WEIGHTED_LEG_CURL")
    built = payload(group_of(plain, 12, 20.0))

    plan = plan_workout(a_workout(exercises=[weighted]), built)

    assert any("its target is lost" in w for w in plan.warnings)


def test_the_addition_carries_the_removal_it_looks_like_a_rename_of():
    """Which is what lets the report say it once, where the exercise now sits."""
    plain = replace(CALF, garmin_name="LEG_CURL", garmin_category=None)
    weighted = replace(plain, garmin_name="WEIGHTED_LEG_CURL")
    built = payload(group_of(plain, 12, 20.0))

    plan = plan_workout(a_workout(exercises=[weighted]), built)

    added = [c for c in plan.structure if c.kind == "added"]
    assert [c.replaces for c in added] == ["LEG_CURL"]


def test_a_paired_removal_is_not_a_change_of_its_own():
    """One exercise replacing another is one line, and so one thing counted."""
    plain = replace(CALF, garmin_name="LEG_CURL", garmin_category=None)
    weighted = replace(plain, garmin_name="WEIGHTED_LEG_CURL")
    built = payload(group_of(plain, 12, 20.0))

    plan = plan_workout(a_workout(exercises=[weighted]), built)

    assert len(plan.structure) == 2
    assert [c.kind for c in plan.reshaped] == ["added"]


def test_two_renames_at_once_are_paired_off_one_to_one():
    """Neither addition may claim both removals, or each would warn twice."""
    curl = replace(CALF, garmin_name="LEG_CURL", garmin_category=None)
    press = replace(
        CALF, name="Leg Press", garmin_name="LEG_PRESS", garmin_category=None
    )
    built = payload(group_of(curl, 12, 20.0), group_of(press, 12, 40.0))

    plan = plan_workout(
        a_workout(
            exercises=[
                replace(curl, garmin_name="WEIGHTED_LEG_CURL"),
                replace(press, garmin_name="WEIGHTED_LEG_PRESS"),
            ]
        ),
        built,
    )

    added = [c for c in plan.structure if c.kind == "added"]
    assert [c.replaces for c in added] == ["LEG_CURL", "LEG_PRESS"]
    assert len(plan.warnings) == 2


def test_swapping_in_a_different_movement_is_not_warned_about():
    """Nothing links a curl to a squat, so this reads as what it is."""
    built = payload(group_of(SQUAT, 7, 20.0))

    plan = plan_workout(a_workout(exercises=[CURLS]), built)

    assert {c.kind for c in plan.structure} == {"added", "removed"}
    assert plan.warnings == []


def test_an_exercise_merely_added_is_not_warned_about():
    """Nothing is being removed, so nothing is at risk."""
    built = payload(group_of(SQUAT, 7, 20.0))

    plan = plan_workout(a_workout(exercises=[SQUAT, CURLS]), built)

    assert [c.kind for c in plan.structure] == ["added"]
    assert plan.warnings == []


# --- the rest between exercises -------------------------------------------


def gaps_of(built):
    """What each step between the exercises prescribes, in order."""
    return [
        step.get("endCondition", {}).get("conditionTypeKey")
        if not is_timed_rest(step)
        else int(step["endConditionValue"])
        for step in built["workoutSegments"][0]["workoutSteps"]
        if step.get("type") != "RepeatGroupDTO"
    ]


def resting(seconds, exercises=(SQUAT, CURLS)):
    return replace(a_workout(exercises=list(exercises)), rest_between=seconds)


def test_the_configured_gap_replaces_a_lap_button_wait():
    """The whole point of the setting: Garmin's default is a button press."""
    built = payload(group_of(SQUAT, 7, 20.0), rest_step(60.0), group_of(CURLS, 10, 7.0))

    plan = plan_workout(resting(45), built)

    assert gaps_of(built) == [45]
    assert plan.gaps is not None
    assert (plan.gaps.gaps, plan.gaps.was, plan.gaps.new) == (1, (None,), 45)
    assert plan.gaps.before == "lap button"


def test_the_configured_gap_retimes_an_existing_countdown():
    built = payload(
        group_of(SQUAT, 7, 20.0), timed_rest(90.0), group_of(CURLS, 10, 7.0)
    )

    plan = plan_workout(resting(45), built)

    assert gaps_of(built) == [45]
    assert plan.gaps is not None
    assert plan.gaps.before == "90 s rest"


def test_a_gap_that_already_matches_is_left_alone():
    """Idempotent: the second run must find nothing to do."""
    built = payload(
        group_of(SQUAT, 7, 20.0), timed_rest(45.0), group_of(CURLS, 10, 7.0)
    )
    for group, each in zip(
        [s for s in built["workoutSegments"][0]["workoutSteps"] if "workoutSteps" in s],
        [SQUAT, CURLS],
        strict=True,
    ):
        group["workoutSteps"][0]["description"] = each.note

    plan = plan_workout(resting(45), built)

    assert plan.gaps is None
    assert not plan.writable


def test_no_configured_gap_leaves_garmins_own_alone():
    """Absent is no opinion, so a button press stays a button press."""
    built = payload(group_of(SQUAT, 7, 20.0), rest_step(60.0), group_of(CURLS, 10, 7.0))

    plan = plan_workout(resting(None), built)

    assert plan.gaps is None
    assert gaps_of(built) == ["lap.button"]


def test_the_gap_is_applied_between_every_pair_and_after_none():
    built = payload(
        group_of(SQUAT, 7, 20.0),
        rest_step(60.0),
        group_of(CURLS, 10, 7.0),
        rest_step(60.0),
        group_of(CALF, 15, 20.0),
    )

    plan_workout(resting(30, (SQUAT, CURLS, CALF)), built)

    assert gaps_of(built) == [30, 30], "two joins, nothing after the last exercise"


def test_a_gap_built_for_a_new_join_starts_at_the_configured_rest():
    """An exercise added at the end needs a gap in front of it, and it should
    not need correcting a moment after it is built."""
    built = payload(group_of(SQUAT, 7, 20.0))

    plan = plan_workout(resting(30), built)

    assert gaps_of(built) == [30]
    assert plan.gaps is None, "built right, so nothing to report as changed"


# --- set counts -----------------------------------------------------------


def test_the_configured_set_count_is_written_to_the_group():
    built = payload(group_of(SQUAT, 7, 20.0, sets=3))

    plan = plan_workout(a_workout(exercises=[replace(SQUAT, sets=5)]), built)

    assert [(c.old, c.new) for c in plan.sets] == [(3, 5)]
    assert next(iter(iter_exercise_blocks(built))).sets == 5
    group = built["workoutSegments"][0]["workoutSteps"][0]
    assert group["endConditionValue"] == 5.0, "Garmin holds the count twice"


def test_dropping_a_set_is_written_too():
    built = payload(group_of(SQUAT, 7, 20.0, sets=4))

    plan = plan_workout(a_workout(exercises=[replace(SQUAT, sets=2)]), built)

    assert [(c.old, c.new) for c in plan.sets] == [(4, 2)]


def test_a_matching_set_count_is_left_alone():
    """Idempotent: the second run must find nothing to do."""
    built = payload(group_of(SQUAT, 7, 20.0))
    built["workoutSegments"][0]["workoutSteps"][0]["workoutSteps"][0]["description"] = (
        SQUAT.note
    )

    plan = plan_workout(a_workout(), built)

    assert plan.sets == []
    assert not plan.writable


def test_an_exercise_with_no_repeat_group_is_reported_rather_than_wrapped():
    """Garmin counts sets as a group's iterations, so a step performed once has
    nowhere to hold them. Building the group is a change of shape, and Connect's
    to make."""
    built = workout(rep_step("BARBELL_BACK_SQUAT", "SQUAT", 7, 20.0))

    plan = plan_workout(a_workout(exercises=[replace(SQUAT, sets=3)]), built)

    assert plan.sets == []
    assert any("no repeat group to count them" in w for w in plan.warnings)


def test_a_newly_built_exercise_needs_no_set_correction():
    built = payload(group_of(SQUAT, 7, 20.0))

    plan = plan_workout(a_workout(exercises=[SQUAT, CURLS]), built)

    assert plan.sets == [], "it was built with the count the config asked for"
    assert next(iter(iter_exercise_blocks(built))).sets == SQUAT.sets


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
    payload = workout(group_of(CALF, 12, 0.0))
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


# --- a session the target has already moved past --------------------------
#
# The rules judge a session against what it was asked for. That is only the
# stored target until something moves it, and this tool moving it is the usual
# case: after `--apply` the same activity is still the latest one.


def a_squat_session(reps=8):
    return performed_sets(
        {"exerciseSets": [active("BARBELL_BACK_SQUAT", "SQUAT", reps, 20000.0)] * 3}
    )


def test_a_session_the_target_has_moved_past_is_left_alone():
    """Stored 9 because this very session earned it; it was performed at 8."""
    payload = workout(rep_step("BARBELL_BACK_SQUAT", "SQUAT", 9, 20.0))
    plan = plan_workout(
        a_workout(),
        payload,
        a_squat_session(8),
        asked={"barbellbacksquat": Target(8, 0.0)},
    )

    assert not plan.moved, "nothing to learn from it twice"
    assert plan.changes[0].reason == "up to date"


def test_a_session_still_aimed_at_the_stored_target_is_judged_normally():
    payload = workout(rep_step("BARBELL_BACK_SQUAT", "SQUAT", 8, 20.0))
    plan = plan_workout(
        a_workout(),
        payload,
        a_squat_session(8),
        asked={"barbellbacksquat": Target(8, 0.0)},
    )

    assert [c.new for c in plan.changes] == [Target(9, 20.0)]


def test_a_ramp_the_session_never_saw_counts_as_moved_past():
    """Same base figure, but the session was not asked for the leading set."""
    payload = workout(
        repeat(rep_step("BARBELL_BACK_SQUAT", "SQUAT", 9, 20.0), sets=1),
        repeat(rep_step("BARBELL_BACK_SQUAT", "SQUAT", 8, 20.0), sets=2),
    )
    plan = plan_workout(
        a_workout(),
        payload,
        a_squat_session(8),
        asked={"barbellbacksquat": Target(8, 0.0)},
    )

    assert not plan.moved


def test_a_hand_edited_target_is_respected_rather_than_judged():
    """You can still set a target by hand; the last session is not evidence."""
    payload = workout(rep_step("BARBELL_BACK_SQUAT", "SQUAT", 6, 20.0))
    plan = plan_workout(
        a_workout(),
        payload,
        a_squat_session(8),
        asked={"barbellbacksquat": Target(8, 0.0)},
    )

    assert not plan.moved
    assert step_target(next(iter(payload["workoutSegments"][0]["workoutSteps"]))) == (
        Target(6, 20.0)
    )


def test_without_an_executed_record_the_stored_target_is_still_assumed():
    """An account that answers nothing degrades to the old behaviour."""
    payload = workout(rep_step("BARBELL_BACK_SQUAT", "SQUAT", 8, 20.0))
    plan = plan_workout(a_workout(), payload, a_squat_session(8), asked={})

    assert [c.new for c in plan.changes] == [Target(9, 20.0)]


# --- two exercises under one category -------------------------------------

STANDING_CALF = spec(
    name="Weighted Standing Calf Raise",
    garmin_name="WEIGHTED_STANDING_CALF_RAISE",
    garmin_category="CALF_RAISE",
    rep_low=15,
    rep_high=18,
    sets=3,
)
SEATED_CALF = replace(
    STANDING_CALF,
    name="Weighted Seated Calf Raise",
    garmin_name="WEIGHTED_SEATED_CALF_RAISE",
    rep_high=20,
    sets=4,
)
CALF_CATALOG = frozenset({"WEIGHTED_STANDING_CALF_RAISE", "WEIGHTED_SEATED_CALF_RAISE"})


def a_calf_workout():
    """A workout Garmin holds as the standing raise."""
    return payload(
        repeat(
            rep_step("WEIGHTED_STANDING_CALF_RAISE", "CALF_RAISE", 16, 25.0),
            sets=3,
        )
    )


def calf_steps(built):
    return [block.step.get("exerciseName") for block in iter_exercise_blocks(built)]


def test_swapping_to_another_exercise_in_the_same_category_rebuilds_the_step():
    """The bug this guards: sets and reps changed while the name did not.

    Both raises are `CALF_RAISE`, so the category fallback used to hand the
    standing step to the seated spec. Everything then updated around a name
    that stayed put, and the watch went on saying standing - the one place the
    swap is actually read.
    """
    built = a_calf_workout()
    plan = plan_workout(
        a_workout(exercises=[SEATED_CALF]), built, ({}, {}), trusted=CALF_CATALOG
    )

    assert calf_steps(built) == ["WEIGHTED_SEATED_CALF_RAISE"]
    assert [(c.kind, c.name) for c in plan.structure] == [
        ("added", "Weighted Seated Calf Raise"),
        ("removed", "WEIGHTED_STANDING_CALF_RAISE"),
    ]


def test_a_rebuilt_step_starts_at_the_bottom_of_its_range():
    """A target earned on the standing raise was never the seated one's."""
    built = a_calf_workout()
    plan_workout(
        a_workout(exercises=[SEATED_CALF]), built, ({}, {}), trusted=CALF_CATALOG
    )

    block = next(iter_exercise_blocks(built))
    assert step_target(block.step) == Target(SEATED_CALF.rep_low, 0.0)


def test_the_same_exercise_under_its_own_name_is_still_reused():
    """Rebuilding is for a different movement, not for every run."""
    built = a_calf_workout()
    plan = plan_workout(
        a_workout(exercises=[STANDING_CALF]), built, ({}, {}), trusted=CALF_CATALOG
    )

    block = next(iter_exercise_blocks(built))
    assert step_target(block.step) == Target(16, 25.0)
    assert [c.kind for c in plan.structure] == []


def test_without_a_catalog_the_category_still_rescues_the_step():
    """No network, no catalog: the older, looser behaviour rather than none."""
    built = a_calf_workout()
    plan_workout(a_workout(exercises=[SEATED_CALF]), built, ({}, {}))

    assert calf_steps(built) == ["WEIGHTED_STANDING_CALF_RAISE"]


# --- partial progression turned off ---------------------------------------
#
# The rules build no ramps with the setting off, so what is left to do here is
# the ones Garmin is still holding from before it was turned off: they are
# evened out, upwards, the next time a run reads the workout.

FLAT_SQUAT = replace(SQUAT, partial_progression=False)


def a_ramped_squat(base=8, lead=1, sets=3, weight=20.0):
    """A squat stored as Garmin holds a ramp: two adjacent groups."""
    return workout(
        repeat(rep_step("BARBELL_BACK_SQUAT", "SQUAT", base + 1, weight), sets=lead),
        repeat(rep_step("BARBELL_BACK_SQUAT", "SQUAT", base, weight), sets=sets - lead),
    )


def test_a_stored_ramp_is_levelled_up_with_no_session_at_all():
    """A config edit is the whole reason, so it need not wait to be trained."""
    built = a_ramped_squat()
    plan = plan_workout(a_workout(exercises=[FLAT_SQUAT]), built)

    assert [c.new for c in plan.moved] == [Target(9, 20.0)]
    assert plan.changes[0].reason == "partial progression is off, levelled up"
    assert [step_target(b.step) for b in iter_exercise_blocks(built)] == [
        Target(9, 20.0)
    ], "one group again, asking nine on every set"


def test_a_session_that_missed_still_levels_the_ramp_up():
    """8,8,8 misses the nine it was asked for, and the target still evens out."""
    built = a_ramped_squat()
    plan = plan_workout(a_workout(exercises=[FLAT_SQUAT]), built, a_squat_session(8))

    assert [c.new for c in plan.moved] == [Target(9, 20.0)]
    assert "missed target" in plan.changes[0].reason
    assert "partial progression is off" in plan.changes[0].reason


def test_a_session_that_hit_advances_from_the_ramp_it_was_asked_for():
    """9,8,8 met the ramp. Judged against the levelled target it would miss."""
    built = a_ramped_squat()
    session = performed_sets(
        {
            "exerciseSets": [
                active("BARBELL_BACK_SQUAT", "SQUAT", reps, 20000.0)
                for reps in (9, 8, 8)
            ]
        }
    )
    plan = plan_workout(a_workout(exercises=[FLAT_SQUAT]), built, session)

    assert [c.new for c in plan.moved] == [Target(9, 20.0)]
    assert "add 1 rep" in plan.changes[0].reason


def test_a_session_with_nothing_to_say_does_not_say_it_beside_a_move():
    """The session had nothing to say; the config moved the target anyway."""
    built = a_ramped_squat()
    plan = plan_workout(
        a_workout(exercises=[FLAT_SQUAT]),
        built,
        a_squat_session(8),
        asked={"barbellbacksquat": Target(8, 0.0)},
    )

    assert [c.new for c in plan.moved] == [Target(9, 20.0)]
    assert plan.changes[0].reason == "partial progression is off, levelled up"


def test_levelling_up_stops_at_the_top_of_the_range():
    """A ramp on top of rep_high has nowhere higher to even out to."""
    built = a_ramped_squat(base=FLAT_SQUAT.rep_high)
    plan = plan_workout(a_workout(exercises=[FLAT_SQUAT]), built)

    assert [c.new for c in plan.moved] == [Target(FLAT_SQUAT.rep_high, 20.0)]


def test_a_ramp_is_left_alone_while_partial_progression_is_on():
    built = a_ramped_squat()
    plan = plan_workout(a_workout(exercises=[SQUAT]), built)

    assert not plan.moved
