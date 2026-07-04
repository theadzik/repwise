"""Mapping between Garmin's JSON and this application's types.

This is the only module that knows Garmin's schema, so it is the only place to
change if Garmin changes theirs.

Two things about Garmin's payloads are easy to get wrong:

* Weight units differ by payload. An activity's exercise set records `weight`
  in grams, while a workout step's `weightValue` is in whatever `weightUnit`
  says, normally kilograms.
* Names differ between payloads. Garmin auto-detects the exercise while you
  lift, so what it logs need not match what the workout programs, and can be
  null. The category survives both, so it is kept as a fallback.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Iterator

from ..progression import PerformedSet, Target

#: Garmin's base unit for strength loads.
GRAMS_PER_KG = 1000.0

#: Written onto a step that has a weight but no unit of its own.
KILOGRAM_UNIT = {"unitId": 8, "unitKey": "kilogram", "factor": GRAMS_PER_KG}


def normalise(name: str) -> str:
    """Reduce a name to letters and digits for loose matching."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


# --- workout definitions ---------------------------------------------------


def iter_workout_steps(workout: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Yield every executable step, descending into repeat groups.

    Sets are modelled as a RepeatGroupDTO wrapping one executable step plus a
    rest step, so a workout holds one step per exercise, not one per set.
    """

    def walk(steps):
        for step in steps or []:
            if step.get("type") == "RepeatGroupDTO" or "workoutSteps" in step:
                yield from walk(step.get("workoutSteps"))
            else:
                yield step

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
