"""Turn a Garmin workout into workouts.yaml content.

Garmin knows less than this tool needs. It stores one target per exercise, not
a range, and has no concept of a weight step or of counting a lunge per side.
So an import fills in everything that can be read from the payload, guesses the
load type from the exercise name, and marks the rest for the user to decide.

Pure: takes payloads, returns text. Nothing here talks to Garmin.
"""

from dataclasses import dataclass

from .garmin.payloads import (
    is_rest,
    is_timed_rest,
    iter_exercise_blocks,
    step_rest,
    step_target,
    steps_between,
)
from .yamlio import dump

#: How many reps above the imported target to suggest as the top of the range.
REP_RANGE_WIDTH = 4
#: The same, for timed holds, in seconds.
TIME_RANGE_WIDTH = 15

#: Substrings that identify a load type, most specific first.
_LOAD_HINTS = (
    ("BARBELL", "barbell"),
    ("DUMBBELL", "dumbbell"),
    ("CABLE", "cable"),
    ("MACHINE", "machine"),
    ("SMITH", "machine"),
)


@dataclass(frozen=True)
class ImportedExercise:
    """One exercise as read out of Garmin, plus what had to be guessed."""

    name: str
    garmin_name: str
    garmin_category: str | None
    sets: int
    rest: int | None
    rep_low: int
    rep_high: int
    load: str
    unit: str
    guessed_load: bool

    @property
    def time_based(self) -> bool:
        return self.unit == "seconds"


@dataclass(frozen=True)
class ImportedWorkout:
    """One Garmin workout, ready to be written out as config."""

    key: str
    garmin_workout_id: str
    activity_prefixes: list[str]
    exercises: list[ImportedExercise]
    #: Seconds between exercises, when every gap in the workout agrees on one.
    #: None when they differ or wait for the lap button, which the config has
    #: no way to say other than by leaving the key out.
    rest_between: int | None = None


def humanise(garmin_name: str) -> str:
    """BARBELL_BACK_SQUAT -> Barbell Back Squat."""
    return " ".join(part.capitalize() for part in garmin_name.split("_") if part)


def guess_load(garmin_name: str, weight: float | None) -> tuple[str, bool]:
    """Best guess at the load type, and whether it was a guess at all.

    Garmin does not record how an exercise is loaded, so the name is the only
    signal. Anything unrecognised with a weight is called a machine, which is
    the least wrong default: it at least progresses.
    """
    upper = garmin_name.upper()
    for hint, load in _LOAD_HINTS:
        if hint in upper:
            return load, True
    if not weight:
        return "bodyweight", True
    return "machine", True


def describe_workout(payload: dict) -> ImportedWorkout:
    """Read a Garmin workout definition into importable form."""
    name = payload.get("workoutName") or "Unnamed workout"

    exercises: list[ImportedExercise] = []
    for block in iter_exercise_blocks(payload):
        garmin_name = block.step.get("exerciseName")
        category = block.step.get("category")
        # Either identifies the exercise; a step carrying neither is not one.
        label = garmin_name or category
        if not label:
            continue

        condition = (block.step.get("endCondition") or {}).get("conditionTypeKey")
        time_based = condition == "time"
        # An exercise part-way up a ramp asks more of its leading sets than of
        # the rest. The lower figure is the one every set has reached, so it is
        # the one to import as the bottom of the range.
        asked = [
            found
            for found in (step_target(step, time_based) for step in block.steps)
            if found is not None
        ]
        if not asked:
            continue  # a step with no target of its own, e.g. a lap.button hold
        target = min(asked, key=lambda found: found.reps)

        load, guessed = guess_load(label, target.weight)
        width = TIME_RANGE_WIDTH if time_based else REP_RANGE_WIDTH

        exercises.append(
            ImportedExercise(
                name=humanise(label),
                garmin_name=label,
                garmin_category=category,
                sets=block.sets,
                rest=block.rest,
                # Garmin has a single target, so it becomes the bottom of the
                # range and the top is a suggestion.
                rep_low=target.reps,
                rep_high=target.reps + width,
                load=load,
                unit="seconds" if time_based else "reps",
                guessed_load=guessed,
            )
        )

    return ImportedWorkout(
        key=name,
        garmin_workout_id=str(payload.get("workoutId") or ""),
        activity_prefixes=[name.lower()],
        exercises=exercises,
        rest_between=_rest_between(payload),
    )


def _rest_between(payload: dict) -> int | None:
    """The rest between exercises, when the whole workout agrees on one.

    A single number is all the config can hold, so a workout whose gaps differ
    - or waits for the lap button, as Garmin's own default does - imports
    without the key rather than with a value that would change the others.
    """
    blocks = list(iter_exercise_blocks(payload))
    gaps = [step for step in steps_between(payload, blocks) if is_rest(step)]
    if not gaps or not all(is_timed_rest(step) for step in gaps):
        return None

    seconds = {step_rest(step) for step in gaps}
    return seconds.pop() if len(seconds) == 1 else None


# --- rendering -------------------------------------------------------------

#: Said once at the top rather than beside every value it applies to: the file
#: is written with a YAML dumper, which has nowhere to put a comment.
HEADER = """\
# Generated by `workout import`. Check it before relying on it:
#
#   - activity_prefixes is seeded from the Garmin workout's own name. Add every
#     name your logged sessions might carry, in every language you use.
#   - Garmin stores a single target rather than a rep range, and records no
#     load type, so rep_high and load had to be inferred. Each exercise says
#     what was guessed about it in its notes.
#
# notes is free text this tool never reads. Clear it once you have checked the
# exercise, or keep your own reminders there.

"""

#: What a fresh config starts with. Every one of these can be edited afterwards;
#: they are here so that an imported file is valid the moment it is written.
DEFAULT_SETTINGS: dict = {
    "garmin": {
        "token_store": "~/.garminconnect",
        "activity_search_limit": 50,
        "dump_dir": ".",
    },
    "weight_steps": {
        "barbell": 2.5,
        "dumbbell": 1.0,
        "cable": 5.0,
        "machine": 5.0,
    },
    "min_weights": {
        "barbell": 12.0,
        "dumbbell": 1.0,
        "cable": 5.0,
        "machine": 5.0,
    },
}


def guesses(exercise: ImportedExercise) -> str:
    """What the user still has to decide about this exercise.

    Carried in the entry's own `notes`, because that is the only place a
    dumped document can say something to a human.
    """
    unit = "s" if exercise.time_based else "reps"
    todo = [
        f"TODO: check rep_high - Garmin stores only "
        f"the current target ({exercise.rep_low} {unit})"
    ]
    if exercise.guessed_load:
        todo.append("TODO: load guessed from the exercise name")
    return ". ".join(todo)


def render_exercise(exercise: ImportedExercise) -> dict:
    """One exercise as the mapping the config holds.

    Built key by key rather than from the dataclass, because the order here is
    the order it is read in, and the optional keys are left out entirely
    instead of written as null.
    """
    entry: dict = {
        "name": exercise.name,
        "garmin_name": exercise.garmin_name,
    }
    if exercise.garmin_category:
        entry["garmin_category"] = exercise.garmin_category
    entry["rep_low"] = exercise.rep_low
    entry["rep_high"] = exercise.rep_high
    entry["sets"] = exercise.sets
    if exercise.rest is not None:
        entry["rest"] = exercise.rest
    entry["load"] = exercise.load
    if exercise.time_based:
        entry["unit"] = "seconds"
    entry["notes"] = guesses(exercise)
    return entry


def render_workout(workout: ImportedWorkout) -> dict:
    entry: dict = {
        "key": workout.key,
        "garmin_workout_id": workout.garmin_workout_id,
        "activity_prefixes": workout.activity_prefixes,
    }
    if workout.rest_between is not None:
        entry["rest_between_exercises"] = workout.rest_between
    entry["exercises"] = [render_exercise(e) for e in workout.exercises]
    return entry


def render_config(workouts: list[ImportedWorkout]) -> str:
    """A complete, valid workouts.yaml for the given workouts."""
    document = {
        "settings": DEFAULT_SETTINGS,
        "workouts": [render_workout(workout) for workout in workouts],
    }
    return HEADER + dump(document)
