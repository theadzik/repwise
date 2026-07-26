"""Turning a Garmin workout into config."""

from test_payloads import rep_step, rest_step

from workout.config import load_config
from workout.importer import (
    describe_workout,
    guess_load,
    humanise,
    render_config,
)


def repeat(step, sets=3, rest=90.0):
    return {
        "type": "RepeatGroupDTO",
        "numberOfIterations": sets,
        "workoutSteps": [
            step,
            {
                "stepType": {"stepTypeKey": "rest"},
                "endCondition": {"conditionTypeKey": "time"},
                "endConditionValue": rest,
            },
        ],
    }


def payload(*groups, name="Workout A", workout_id=987654321):
    return {
        "workoutId": workout_id,
        "workoutName": name,
        "workoutSegments": [{"workoutSteps": list(groups)}],
    }


SQUAT = repeat(rep_step("BARBELL_BACK_SQUAT", "SQUAT", 6, 30.0), sets=4, rest=120.0)
PLANK = {
    "type": "RepeatGroupDTO",
    "numberOfIterations": 3,
    "workoutSteps": [
        {
            "exerciseName": "PLANK",
            "category": "PLANK",
            "endCondition": {"conditionTypeKey": "time"},
            "endConditionValue": 47.0,
        },
        rest_step(),
    ],
}


# --- naming ---------------------------------------------------------------


def test_humanise_turns_an_identifier_into_a_label():
    assert humanise("BARBELL_BACK_SQUAT") == "Barbell Back Squat"


def test_load_is_guessed_from_the_name():
    assert guess_load("BARBELL_BACK_SQUAT", 30.0)[0] == "barbell"
    assert guess_load("STANDING_ALTERNATING_DUMBBELL_CURLS", 8.0)[0] == "dumbbell"
    assert guess_load("CABLE_OVERHEAD_TRICEPS_EXTENSION", 15.0)[0] == "cable"


def test_unloaded_exercises_are_called_bodyweight():
    assert guess_load("PLANK", 0.0)[0] == "bodyweight"
    assert guess_load("SIT_UP", None)[0] == "bodyweight"


def test_an_unrecognised_loaded_exercise_falls_back_to_machine():
    load, guessed = guess_load("LAT_PULLDOWN", 40.0)
    assert load == "machine"
    assert guessed, "must be flagged so the user checks it"


# --- reading a workout ----------------------------------------------------


def test_reads_sets_rest_and_target():
    workout = describe_workout(payload(SQUAT))
    assert workout.key == "Workout A"
    assert workout.garmin_workout_id == "987654321"

    squat = workout.exercises[0]
    assert (squat.sets, squat.rest) == (4, 120)
    assert squat.rep_low == 6, "Garmin's target becomes the bottom of the range"
    assert squat.rep_high > squat.rep_low, "the top is a suggestion"
    assert squat.unit == "reps"


def test_timed_holds_are_recognised():
    workout = describe_workout(payload(PLANK))
    plank = workout.exercises[0]
    assert plank.unit == "seconds" and plank.time_based
    assert plank.rep_low == 47
    assert plank.load == "bodyweight"


def test_activity_prefix_is_seeded_from_the_workout_name():
    workout = describe_workout(payload(SQUAT, name="Trening A"))
    assert workout.activity_prefixes == ["trening a"]


def test_steps_without_a_target_are_skipped():
    """A lap.button step prescribes nothing to progress."""
    stray = {
        "exerciseName": "BURPEE",
        "category": "BURPEE",
        "endCondition": {"conditionTypeKey": "lap.button"},
        "endConditionValue": 1.0,
    }
    workout = describe_workout(payload(SQUAT, repeat(stray)))
    assert [e.garmin_name for e in workout.exercises] == ["BARBELL_BACK_SQUAT"]


# --- rendering ------------------------------------------------------------


def test_rendered_config_is_valid_and_loadable(tmp_path):
    """The whole point: paste it in and it works."""
    text = render_config([describe_workout(payload(SQUAT, PLANK))])
    path = tmp_path / "workouts.yaml"
    path.write_text(text)

    config = load_config(str(path))
    workout = config["Workout A"]
    assert workout.garmin_workout_id == "987654321"

    squat, plank = workout.exercises
    assert squat.sets == 4 and squat.rest == 120
    assert squat.weight_step > 0, "load type resolved to a real weight step"
    assert plank.time_based and plank.bodyweight


def test_rendered_config_flags_what_was_inferred():
    text = render_config([describe_workout(payload(SQUAT))])
    assert "TODO: top of your range" in text
    assert "TODO: guessed from the exercise name" in text


def test_several_workouts_render_together(tmp_path):
    workouts = [
        describe_workout(payload(SQUAT, name="Workout A", workout_id=1)),
        describe_workout(payload(SQUAT, name="Workout B", workout_id=2)),
    ]
    path = tmp_path / "workouts.yaml"
    path.write_text(render_config(workouts))
    assert set(load_config(str(path)).workouts) == {"Workout A", "Workout B"}
