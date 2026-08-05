"""Mapping between Garmin's JSON and this application's types.

This is the only module that knows Garmin's schema, so it is the only place to
change if Garmin changes theirs.

Two things about Garmin's payloads are easy to get wrong:

* Weight units differ by payload. An activity's exercise set records `weight`
  in grams, while a workout step's `weightValue` is in whatever `weightUnit`
  says, normally kilograms.
* Names differ between payloads. Garmin auto-detects the exercise while you
  lift, so what it logs need not match what the workout programs, and can be
  null. The category survives both, so it is kept as a fallback. Deciding
  which exercise a name refers to is `domain/matching.py`, not this module.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from ..domain.matching import normalise
from ..domain.progression import PerformedSet, Target

#: Garmin's base unit for strength loads.
GRAMS_PER_KG = 1000.0

#: Written onto a step that has a weight but no unit of its own.
KILOGRAM_UNIT = {"unitId": 8, "unitKey": "kilogram", "factor": GRAMS_PER_KG}

#: Garmin's per-step notes field. Connect labels it "Notes" and the watch
#: surfaces it as WorkoutStepInfo.notes; in the JSON it is `description`, one
#: per ExecutableStepDTO, null until something writes it.
NOTE_FIELD = "description"

#: The shape ExerciseSpec.note renders. Used to tell a note this tool wrote
#: from a cue the user typed, so that only the former is ever overwritten.
GENERATED_NOTE = re.compile(
    r"^\d+-\d+ (?:reps|s)(?: by \d+)? \| (?:bodyweight|\+[\d.]+ kg)$"
)


# --- workout definitions ---------------------------------------------------


@dataclass(frozen=True)
class ExerciseBlock:
    """An exercise step together with the repeat group that surrounds it.

    A workout is a tree, and every caller needs the same two things out of it:
    the exercise step itself, and what the repeat group around it says - how
    many iterations it prescribes, and the rest step alongside it.

    The rest step is kept rather than the seconds it holds, because the planner
    writes to it as well as reads it; `rest` stays honest about what the step
    currently says either way.
    """

    step: dict[str, Any]
    sets: int
    rest_step: dict[str, Any] | None

    @property
    def rest(self) -> int | None:
        """Seconds between sets, or None when Garmin prescribes no interval."""
        return None if self.rest_step is None else step_rest(self.rest_step)


def _rest_step(steps: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The rest step of a repeat group, when it prescribes a fixed time."""
    for step in steps:
        if (step.get("stepType") or {}).get("stepTypeKey") != "rest":
            continue
        end = step.get("endCondition") or {}
        # A lap.button rest has no duration, only a prompt to press the button.
        if end.get("conditionTypeKey") == "time" and step.get("endConditionValue"):
            return step
    return None


def iter_exercise_blocks(workout: dict[str, Any]) -> Iterator[ExerciseBlock]:
    """Yield each exercise with its set count and rest step.

    Sets are modelled as a RepeatGroupDTO wrapping one executable step plus a
    rest step, so a workout holds one step per exercise, not one per set. The
    rest steps and any step with neither a name nor a category are skipped:
    what comes out is one block per exercise, in the order they are performed.
    """

    def walk(steps: list[dict[str, Any]] | None) -> Iterator[ExerciseBlock]:
        for step in steps or []:
            children = step.get("workoutSteps")
            if children:
                sets = int(step.get("numberOfIterations") or 1)
                rest = _rest_step(children)
                for inner in walk(children):
                    # An inner repeat group keeps its own count and rest.
                    yield ExerciseBlock(
                        inner.step,
                        inner.sets if inner.sets > 1 else sets,
                        inner.rest_step if inner.rest_step is not None else rest,
                    )
            elif step.get("exerciseName") or step.get("category"):
                yield ExerciseBlock(step, 1, None)

    for segment in workout.get("workoutSegments") or []:
        yield from walk(segment.get("workoutSteps"))


def step_exercise_name(step: dict[str, Any]) -> str | None:
    return step.get("exerciseName")


def step_category(step: dict[str, Any]) -> str | None:
    return step.get("category")


def step_weight_factor(step: dict[str, Any]) -> float:
    """Grams per unit of this step's weightValue.

    Steps carry their own unit, so weightValue is *not* in grams the way the
    activity payload is. Default to kilograms when the unit is missing.
    """
    unit = step.get("weightUnit") or {}
    return float(unit.get("factor") or GRAMS_PER_KG)


def step_target(step: dict[str, Any], time_based: bool = False) -> Target | None:
    """Current prescription stored on a workout step.

    Timed holds end on `time` rather than `reps`, and their endConditionValue
    is seconds. Both live in the same field, so only the condition differs.
    """
    end = step.get("endCondition") or {}
    if end.get("conditionTypeKey") != ("time" if time_based else "reps"):
        return None
    value = step.get("endConditionValue")
    if value is None:
        return None
    if time_based:
        return Target(int(value), 0.0)
    raw = step.get("weightValue")
    kg = 0.0 if raw is None else float(raw) * step_weight_factor(step) / GRAMS_PER_KG
    return Target(int(value), round(kg, 3))


def step_rest(step: dict[str, Any]) -> int:
    """Seconds a rest step prescribes.

    Only meaningful for the steps `ExerciseBlock.rest_step` holds, which are
    the rests that end on a time rather than on the lap button.
    """
    return int(step["endConditionValue"])


def step_note(step: dict[str, Any]) -> str:
    """The step's notes field. Absent, null and empty all read as no note."""
    return step.get(NOTE_FIELD) or ""


def apply_note(step: dict[str, Any], text: str) -> None:
    """Write the notes field of a workout step, in place."""
    step[NOTE_FIELD] = text


def apply_rest(step: dict[str, Any], seconds: int) -> None:
    """Write a new interval onto a rest step, in place.

    Only the duration changes: the step already ends on a time, which is what
    made it writable, so nothing about the shape of the workout moves.
    """
    step["endConditionValue"] = float(seconds)


def apply_target(step: dict[str, Any], target: Target) -> None:
    """Write a new prescription onto a workout step, in place."""
    step["endConditionValue"] = float(target.reps)
    if target.weight > 0:
        step["weightValue"] = target.weight * GRAMS_PER_KG / step_weight_factor(step)
        # Not setdefault: an unloaded step carries weightUnit explicitly set to
        # null, which setdefault would leave in place.
        if not step.get("weightUnit"):
            step["weightUnit"] = dict(KILOGRAM_UNIT)


# --- logged activities -----------------------------------------------------


def performed_sets(
    sets_payload: dict[str, Any],
) -> tuple[dict[str, list[PerformedSet]], dict[str, list[PerformedSet]]]:
    """Index the working sets by exercise name and, separately, by category."""
    by_name: dict[str, list[PerformedSet]] = defaultdict(list)
    by_category: dict[str, list[PerformedSet]] = defaultdict(list)

    for entry in sets_payload.get("exerciseSets") or []:
        if entry.get("setType") != "ACTIVE":
            continue  # skip rest sets
        reps = entry.get("repetitionCount")
        if not reps:
            continue
        exercises = entry.get("exercises") or []
        if not exercises:
            continue

        grams = entry.get("weight") or 0.0
        logged = PerformedSet(
            int(reps),
            round(float(grams) / GRAMS_PER_KG, 3),
            float(entry.get("duration") or 0.0),
        )

        name = exercises[0].get("name")
        category = exercises[0].get("category")
        if name:
            by_name[normalise(name)].append(logged)
        if category:
            by_category[normalise(category)].append(logged)

    return dict(by_name), dict(by_category)
