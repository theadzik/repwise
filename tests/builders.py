"""Test data: the payloads Garmin sends, and the config that describes them.

One place for the builders, so that a test module needing a workout step does
not import it from whichever other test module happened to define it first.
The payloads here are trimmed copies of real Garmin responses.

Fixtures live in conftest.py; everything here is a plain function or constant,
imported by name.
"""

import os

from workout.domain.models import ExerciseSpec
from workout.domain.progression import PerformedSet
from workout.garmin.catalog import ExerciseCatalog

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
#: The shipped example. A user's own workouts.yaml is gitignored, so this
#: is the only config guaranteed to exist in a fresh checkout.
EXAMPLE_CONFIG = os.path.join(REPO_ROOT, "workouts.example.yaml")

#: Garmin's kilogram unit, as it appears on a workout step.
KILOGRAM = {"unitId": 8, "unitKey": "kilogram", "factor": 1000.0}

#: Every key Garmin returns on a repeat group and on an executable step, copied
#: from a real workout payload. A built step may use fewer - Garmin fills the
#: rest with null - but never a key that is not here, which is what makes this
#: worth keeping: a typo in a field name is otherwise silently ignored.
GARMIN_GROUP_KEYS = {
    "childStepId",
    "endCondition",
    "endConditionCompare",
    "endConditionValue",
    "numberOfIterations",
    "preferredEndConditionUnit",
    "skipLastRestStep",
    "smartRepeat",
    "stepId",
    "stepOrder",
    "stepType",
    "type",
    "workoutSteps",
}
GARMIN_STEP_KEYS = {
    "category",
    "childStepId",
    "description",
    "endCondition",
    "endConditionCompare",
    "endConditionValue",
    "endConditionZone",
    "equipmentType",
    "exerciseName",
    "preferredEndConditionUnit",
    "providerExerciseSourceId",
    "secondaryTargetType",
    "secondaryTargetValueOne",
    "secondaryTargetValueTwo",
    "secondaryTargetValueUnit",
    "secondaryZoneNumber",
    "stepId",
    "stepOrder",
    "stepType",
    "strokeType",
    "targetType",
    "targetValueOne",
    "targetValueTwo",
    "targetValueUnit",
    "type",
    "weightUnit",
    "weightValue",
    "workoutProvider",
    "zoneNumber",
}


# --- the domain -----------------------------------------------------------


def spec(**kwargs) -> ExerciseSpec:
    """An ExerciseSpec with sensible defaults, overridable per test."""
    base = {
        "name": "Barbell Back Squat",
        "garmin_name": "BARBELL_BACK_SQUAT",
        "garmin_category": "SQUAT",
        "rep_low": 6,
        "rep_high": 10,
        "sets": 3,
        "load": "barbell",
        "weight_step": 5.0,
    }
    return ExerciseSpec(**{**base, **kwargs})


def held(*seconds: float) -> list[PerformedSet]:
    """Timed sets as Garmin logs them: 1 rep, real figure in the duration."""
    return [PerformedSet(1, 0.0, s).as_time() for s in seconds]


# --- Garmin's exercise catalog --------------------------------------------


def catalog_payload(**categories: tuple[str, ...]) -> dict:
    """The catalog as Garmin serves it, trimmed to the exercises named.

    The muscle groups are carried because the real file carries them: nothing
    reads them yet, and a parser that only tolerated the fields it uses would
    be the wrong thing to have written.
    """
    return {
        "categories": {
            category: {
                "exercises": {
                    name: {"primaryMuscles": ["ABS"], "secondaryMuscles": []}
                    for name in names
                }
            }
            for category, names in categories.items()
        }
    }


def catalog(**categories: tuple[str, ...]) -> ExerciseCatalog:
    """A parsed catalog holding just what a test names."""
    return ExerciseCatalog.parse(catalog_payload(**categories))


#: What the specs in this file claim, so that a test about something else does
#: not trip the name checks on its way past.
CATALOG = catalog(
    SQUAT=("BARBELL_BACK_SQUAT", "FRONT_SQUAT"),
    PLANK=("PLANK",),
    DEADLIFT=("BARBELL_DEADLIFT",),
    ROW=("FACE_PULL",),
)


# --- workout definitions --------------------------------------------------


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
    """The rest between exercises: a prompt to press the lap button.

    Garmin stores a duration beside it, which the watch ignores.
    """
    return {
        "type": "ExecutableStepDTO",
        "stepType": {"stepTypeKey": "rest"},
        "endCondition": {"conditionTypeKey": "lap.button"},
        "endConditionValue": seconds,
    }


def timed_rest(seconds=90.0):
    """The rest inside a repeat group: a countdown between sets."""
    return {
        "type": "ExecutableStepDTO",
        "stepType": {"stepTypeKey": "rest"},
        "endCondition": {"conditionTypeKey": "time"},
        "endConditionValue": seconds,
    }


def workout(*steps):
    return {"workoutSegments": [{"workoutSteps": list(steps)}]}


def repeat(step, sets=3, rest=90.0):
    """A step wrapped in the repeat group Garmin uses to express sets."""
    return {
        "type": "RepeatGroupDTO",
        "numberOfIterations": sets,
        "workoutSteps": [step, timed_rest(rest)],
    }


def payload(*groups, name="Workout A", workout_id=987654321):
    """A whole workout definition, as `get_workout_by_id` returns one."""
    return {
        "workoutId": workout_id,
        "workoutName": name,
        "workoutSegments": [{"workoutSteps": list(groups)}],
    }


# --- logged activities ----------------------------------------------------


def active(name, category, reps, grams, duration=40.0):
    return {
        "setType": "ACTIVE",
        "repetitionCount": reps,
        "weight": grams,
        "duration": duration,
        "exercises": [{"name": name, "category": category}],
    }


# --- config text ----------------------------------------------------------

FIXTURE = """
settings:
  weight_steps:
    barbell: 5.0
    dumbbell: 1.0

workouts:
  - key: Workout A
    garmin_workout_id: "123"
    activity_prefixes: ["Training A"]
    exercises:
      - name: Barbell Back Squat
        garmin_name: BARBELL_BACK_SQUAT
        garmin_category: SQUAT
        rep_low: 6
        rep_high: 10
        sets: 4
        rest: 120
        load: barbell
      - name: Plank
        garmin_name: PLANK
        garmin_category: PLANK
        rep_low: 30
        rep_high: 60
        sets: 3
        load: bodyweight
        unit: seconds
"""
