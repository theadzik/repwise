"""Mapping Garmin's JSON to and from this application's types."""

from builders import active, rep_step, rest_step, spec, timed_rest, workout

from workout.domain.progression import Target
from workout.garmin.payloads import (
    GENERATED_NOTE,
    apply_note,
    apply_rest,
    apply_target,
    iter_exercise_blocks,
    performed_sets,
    step_note,
    step_rest,
    step_target,
)

# --- walking the structure ------------------------------------------------


def test_iter_descends_into_repeat_groups():
    """Sets are a RepeatGroupDTO wrapping the exercise plus a rest step."""
    payload = workout(
        {
            "type": "RepeatGroupDTO",
            "numberOfIterations": 4,
            "workoutSteps": [
                rep_step("BARBELL_BACK_SQUAT", "SQUAT", 6, 30.0),
                timed_rest(90.0),
            ],
        }
    )
    blocks = list(iter_exercise_blocks(payload))

    assert len(blocks) == 1, "the rest step is not an exercise of its own"
    assert blocks[0].step["exerciseName"] == "BARBELL_BACK_SQUAT"
    assert blocks[0].sets == 4
    assert blocks[0].rest == 90


def test_a_step_outside_a_repeat_group_is_one_set_and_no_rest():
    payload = workout(rep_step("BARBELL_BACK_SQUAT", "SQUAT", 6, 30.0))
    block = next(iter(iter_exercise_blocks(payload)))

    assert (block.sets, block.rest, block.rest_step) == (1, None, None)


def test_a_zero_second_rest_is_a_duration_like_any_other():
    """Falsy but present: a step that can hold a rest, currently holding none."""
    payload = workout(
        {
            "type": "RepeatGroupDTO",
            "numberOfIterations": 3,
            "workoutSteps": [
                rep_step("BARBELL_BACK_SQUAT", "SQUAT", 6, 30.0),
                timed_rest(0.0),
            ],
        }
    )
    block = next(iter(iter_exercise_blocks(payload)))

    assert block.rest == 0
    assert block.rest_step is not None, "a configured rest can be written here"


def test_a_timed_rest_with_no_value_reads_as_no_rest():
    payload = workout(
        {
            "type": "RepeatGroupDTO",
            "numberOfIterations": 3,
            "workoutSteps": [
                rep_step("BARBELL_BACK_SQUAT", "SQUAT", 6, 30.0),
                {
                    "stepType": {"stepTypeKey": "rest"},
                    "endCondition": {"conditionTypeKey": "time"},
                    "endConditionValue": None,
                },
            ],
        }
    )
    assert next(iter(iter_exercise_blocks(payload))).rest_step is None


def test_a_lap_button_rest_is_no_interval_at_all():
    """It prompts you to press the button; the value beside it means nothing."""
    payload = workout(
        {
            "type": "RepeatGroupDTO",
            "numberOfIterations": 3,
            "workoutSteps": [rep_step("PLANK", "PLANK", 30, None), rest_step(60.0)],
        }
    )
    block = next(iter(iter_exercise_blocks(payload)))

    assert block.rest is None
    assert block.rest_step is None, "nothing to write a configured rest onto"


# --- reading targets ------------------------------------------------------


def test_workout_step_weight_is_kilograms_not_grams():
    """weightValue 30.0 with a kilogram unit is 30 kg, not 0.03 kg."""
    step = rep_step("BARBELL_BACK_SQUAT", "SQUAT", 6, 30.0)
    assert step_target(step) == Target(6, 30.0)


def test_gram_unit_is_converted():
    gram = {"unitId": 1, "unitKey": "gram", "factor": 1.0}
    step = rep_step("BARBELL_BACK_SQUAT", "SQUAT", 6, 20000.0, unit=gram)
    assert step_target(step) == Target(6, 20.0)


def test_missing_unit_is_assumed_kilograms():
    step = rep_step("STANDING_CALF_RAISE", "CALF_RAISE", 12, 20.0, unit=None)
    assert step_target(step) == Target(12, 20.0)


def test_missing_weight_reads_as_zero():
    step = rep_step("STANDING_CALF_RAISE", "CALF_RAISE", 12, None)
    assert step_target(step) == Target(12, 0.0)


def test_rest_steps_have_no_target():
    assert step_target(rest_step()) is None


def test_timed_step_needs_the_time_flag():
    step = {
        "exerciseName": "PLANK",
        "category": "PLANK",
        "endCondition": {"conditionTypeKey": "time"},
        "endConditionValue": 47.0,
    }
    assert step_target(step) is None, "a timed hold is not a rep target"
    assert step_target(step, time_based=True) == Target(47, 0.0)


# --- writing targets ------------------------------------------------------


def test_apply_round_trips_through_the_unit():
    step = rep_step("BARBELL_BACK_SQUAT", "SQUAT", 6, 30.0)
    apply_target(step, Target(8, 32.5))
    assert step_target(step) == Target(8, 32.5)


def test_apply_adds_a_unit_when_the_step_has_none():
    step = rep_step("STANDING_CALF_RAISE", "CALF_RAISE", 12, None, unit=None)
    apply_target(step, Target(12, 20.0))
    assert step["weightUnit"]["unitKey"] == "kilogram"
    assert step_target(step) == Target(12, 20.0)


def test_apply_rest_changes_only_the_duration():
    """The step already ends on a time, so nothing about its shape moves."""
    step = timed_rest(90.0)
    apply_rest(step, 150)

    assert step_rest(step) == 150
    assert step["endCondition"] == {"conditionTypeKey": "time"}


def test_a_written_rest_reads_back_through_the_block():
    """What the planner relies on to leave an already-correct rest alone."""
    payload = workout(
        {
            "type": "RepeatGroupDTO",
            "numberOfIterations": 3,
            "workoutSteps": [
                rep_step("BARBELL_BACK_SQUAT", "SQUAT", 6, 30.0),
                timed_rest(90.0),
            ],
        }
    )
    block = next(iter(iter_exercise_blocks(payload)))
    apply_rest(block.rest_step, 120)

    assert block.rest == 120


def test_apply_leaves_bodyweight_unloaded():
    step = {
        "exerciseName": "PLANK",
        "category": "PLANK",
        "endCondition": {"conditionTypeKey": "time"},
        "endConditionValue": 47.0,
    }
    apply_target(step, Target(48, 0.0))
    assert step["endConditionValue"] == 48.0
    assert "weightValue" not in step


# --- notes ----------------------------------------------------------------
#
# Garmin calls this field `description` on the step; Connect labels it "Notes"
# and the watch reads it as WorkoutStepInfo.notes. Verified by round-tripping
# a value through update_workout against a real account.


def test_note_is_absent_null_and_empty_alike():
    assert step_note({}) == ""
    assert step_note({"description": None}) == ""
    assert step_note({"description": ""}) == ""


def test_apply_note_writes_the_description_field():
    step = rep_step("BARBELL_BACK_SQUAT", "SQUAT", 6, 30.0)
    apply_note(step, "6-10 reps | +5 kg")
    assert step["description"] == "6-10 reps | +5 kg"
    assert step_note(step) == "6-10 reps | +5 kg"


def test_note_does_not_disturb_the_target():
    """Notes and targets live in different fields and must not interfere."""
    step = rep_step("BARBELL_BACK_SQUAT", "SQUAT", 6, 30.0)
    apply_note(step, "6-10 reps | +5 kg")
    assert step_target(step) == Target(6, 30.0)


def test_every_rendered_note_is_recognised_as_generated():
    """Whatever ExerciseSpec.note produces must match the pattern that
    decides a note is safe to overwrite, or the tool would refuse to update
    its own notes."""
    cases = [
        spec(),  # barbell reps
        spec(rep_step=2, rep_low=16, rep_high=24),  # per-side step
        spec(load="bodyweight", weight_step=0.0),  # bodyweight
        spec(load="bodyweight", weight_step=0.0, unit="seconds"),  # timed hold
        spec(load="dumbbell", weight_step=1.0),  # fractional step
        spec(load="cable", weight_step=2.5),
    ]
    for each in cases:
        assert GENERATED_NOTE.match(each.note), each.note


def test_a_hand_written_note_is_not_mistaken_for_a_generated_one():
    for text in [
        "elbows tucked",
        "6-10 reps",  # truncated, missing the load half
        "6-10 reps | +5 kg  elbows in",  # ours plus a hand-added cue
        "keep 6-10 reps | +5 kg",
    ]:
        assert not GENERATED_NOTE.match(text), text


# --- reading performed sets -----------------------------------------------


def test_activity_weight_is_grams():
    by_name, _ = performed_sets(
        {"exerciseSets": [active("SQUAT", "SQUAT", 9, 20000.0)]}
    )
    assert by_name["squat"][0].weight == 20.0


def test_rest_sets_are_skipped():
    payload = {
        "exerciseSets": [
            active("SQUAT", "SQUAT", 9, 20000.0),
            {"setType": "REST", "repetitionCount": 0, "exercises": []},
        ]
    }
    by_name, _ = performed_sets(payload)
    assert len(by_name["squat"]) == 1


def test_sets_are_indexed_by_category_too():
    """Garmin can log a null name; the category still identifies it."""
    payload = {
        "exerciseSets": [
            {
                "setType": "ACTIVE",
                "repetitionCount": 11,
                "weight": 10000.0,
                "exercises": [{"name": None, "category": "TRICEPS_EXTENSION"}],
            }
        ]
    }
    by_name, by_category = performed_sets(payload)
    assert by_name == {}
    assert by_category["tricepsextension"][0].reps == 11


def test_duration_is_kept_for_timed_holds():
    payload = {"exerciseSets": [active("PLANK", "PLANK", 1, 0.0, duration=46.0)]}
    by_name, _ = performed_sets(payload)
    assert by_name["plank"][0].as_time().reps == 46
