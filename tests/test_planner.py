"""Matching workout steps to exercises, and planning the updates."""

from copy import deepcopy
from dataclasses import replace

import pytest
from builders import active, payload, rep_step, repeat, rest_step, spec, workout

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
