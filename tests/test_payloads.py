"""Mapping Garmin's JSON to and from this application's types.

The payloads below are trimmed copies of real Garmin responses.
"""

from workout.garmin.payloads import (
    apply_target,
    iter_workout_steps,
    normalise,
    performed_sets,
    step_target,
)
from workout.progression import Target

KILOGRAM = {"unitId": 8, "unitKey": "kilogram", "factor": 1000.0}


def rep_step(name, category, reps, weight, unit=KILOGRAM):
    return {
        "type": "ExecutableStepDTO",
        "exerciseName": name,
        "category": category,
        "endCondition": {"conditionTypeKey": "reps"},
        "endConditionValue": float(reps),
        "weightValue": weight,
        "weightUnit": unit,
    }


def rest_step(seconds=60.0):
    return {
        "type": "ExecutableStepDTO",
        "stepType": {"stepTypeKey": "rest"},
        "endCondition": {"conditionTypeKey": "lap.button"},
        "endConditionValue": seconds,
    }


def workout(*steps):
    return {"workoutSegments": [{"workoutSteps": list(steps)}]}


# --- walking the structure ------------------------------------------------


def test_iter_descends_into_repeat_groups():
    """Sets are a RepeatGroupDTO wrapping the exercise plus a rest step."""
    payload = workout(
        {
            "type": "RepeatGroupDTO",
            "numberOfIterations": 4,
            "workoutSteps": [
                rep_step("BARBELL_BACK_SQUAT", "SQUAT", 6, 30.0),
                rest_step(),
            ],
        }
    )
    steps = list(iter_workout_steps(payload))
    assert len(steps) == 2
    assert steps[0]["exerciseName"] == "BARBELL_BACK_SQUAT"


def test_normalise_bridges_the_two_naming_styles():
    assert normalise("BARBELL_BACK_SQUAT") == normalise("Barbell Back Squat")


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


# --- reading performed sets -----------------------------------------------


def active(name, category, reps, grams, duration=40.0):
    return {
        "setType": "ACTIVE",
        "repetitionCount": reps,
        "weight": grams,
        "duration": duration,
        "exercises": [{"name": name, "category": category}],
    }


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
