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
from ..domain.models import ExerciseSpec
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

# Garmin names the parts of a workout by id and by key together, and returns
# them that way. Sending the same objects is what keeps a workout built here
# indistinguishable from one built in Connect.
SPORT_STRENGTH = {"sportTypeId": 5, "sportTypeKey": "strength_training"}
STEP_INTERVAL = {"stepTypeId": 3, "stepTypeKey": "interval"}
STEP_REST = {"stepTypeId": 5, "stepTypeKey": "rest"}
STEP_REPEAT = {"stepTypeId": 6, "stepTypeKey": "repeat"}
END_LAP_BUTTON = {"conditionTypeId": 1, "conditionTypeKey": "lap.button"}
END_TIME = {"conditionTypeId": 2, "conditionTypeKey": "time"}
END_ITERATIONS = {"conditionTypeId": 7, "conditionTypeKey": "iterations"}
END_REPS = {"conditionTypeId": 10, "conditionTypeKey": "reps"}

#: What Connect puts on a step with nothing to aim at. Garmin accepts its
#: absence and stores null, so this is for likeness rather than function.
NO_TARGET = {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"}


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
    """The rest step of a repeat group, when it prescribes a fixed time.

    The duration is tested against None rather than for truth, as `step_target`
    tests its own: what makes a rest unreadable is ending on the lap button or
    carrying no value at all. Zero seconds is a duration like any other, and
    treating it as absent would refuse to write a configured rest onto a step
    that can hold one perfectly well.
    """
    for step in steps:
        if (step.get("stepType") or {}).get("stepTypeKey") != "rest":
            continue
        end = step.get("endCondition") or {}
        # A lap.button rest has no duration, only a prompt to press the button.
        if (
            end.get("conditionTypeKey") == "time"
            and step.get("endConditionValue") is not None
        ):
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


# --- building a workout ----------------------------------------------------
#
# Everything above reads or edits what Garmin already holds. These build the
# same shapes from nothing, for a workout Garmin has not been told about, or an
# exercise added to one it has. Garmin fills every field left out with null and
# returns the key set Connect sends, so only what carries meaning is written
# here - see docs/garmin-api.md.


def new_workout(name: str) -> dict[str, Any]:
    """The shell of a strength workout, with one empty segment to fill.

    No id: that is Garmin's to issue, and its absence is what distinguishes a
    workout to create from one to replace.
    """
    return {
        "workoutName": name,
        "sportType": dict(SPORT_STRENGTH),
        "workoutSegments": [
            {
                "segmentOrder": 1,
                "sportType": dict(SPORT_STRENGTH),
                "workoutSteps": [],
            }
        ],
    }


def new_rest(seconds: int | None) -> dict[str, Any]:
    """A rest step: a countdown, or a wait for the lap button when None.

    The same shape wherever it sits - between the sets of one exercise, or
    between two exercises. Only its position in the tree says which it is.
    """
    if seconds is None:
        return {
            "type": "ExecutableStepDTO",
            "stepType": dict(STEP_REST),
            "endCondition": dict(END_LAP_BUTTON),
            "endConditionValue": None,
        }
    return {
        "type": "ExecutableStepDTO",
        "stepType": dict(STEP_REST),
        "endCondition": dict(END_TIME),
        "endConditionValue": float(seconds),
    }


def new_group(spec: ExerciseSpec, target: Target) -> dict[str, Any]:
    """A repeat group holding one exercise and the rest that follows each set.

    Sets are the group's iterations, so the exercise appears once however many
    times it is performed. An exercise with no `rest` configured gets a
    lap-button rest rather than no rest step at all: Connect builds one either
    way, and a step that is there can be given a duration later.
    """
    step: dict[str, Any] = {
        "type": "ExecutableStepDTO",
        "stepType": dict(STEP_INTERVAL),
        "endCondition": dict(END_TIME if spec.time_based else END_REPS),
        "category": spec.garmin_category,
        "exerciseName": spec.garmin_name,
        "targetType": dict(NO_TARGET),
        "targetValueTwo": 0.0,
    }
    apply_target(step, target)
    apply_note(step, spec.note)

    return {
        "type": "RepeatGroupDTO",
        "stepType": dict(STEP_REPEAT),
        "numberOfIterations": spec.sets,
        "endCondition": dict(END_ITERATIONS),
        "endConditionValue": float(spec.sets),
        "smartRepeat": False,
        "skipLastRestStep": False,
        "workoutSteps": [step, new_rest(spec.rest or None)],
    }


def set_exercise_steps(
    payload: dict[str, Any],
    groups: list[dict[str, Any]],
    gaps: list[dict[str, Any]],
) -> None:
    """Lay out group, gap, group ... as the workout's steps, and renumber.

    One gap per join, so nothing follows the last exercise: a workout ends when
    its last set does. Each gap must be a step of its own - they are numbered
    individually, so the same dict passed twice would leave two positions
    claimed by one step.

    Groups are placed as given rather than copied, so a group taken out of a
    fetched workout keeps its identity, its ids, and the target stored in it.
    """
    steps: list[dict[str, Any]] = []
    for position, group in enumerate(groups):
        if position:
            steps.append(gaps[position - 1])
        steps.append(group)

    payload["workoutSegments"][0]["workoutSteps"] = steps
    renumber(payload)


def renumber(payload: dict[str, Any]) -> None:
    """Number the steps the way Garmin does, in place.

    `stepOrder` runs 1..N depth-first, counting groups and their children
    alike. `childStepId` counts the groups, and a group's children carry its
    number; a step outside a group has none.

    Garmin sorts by `stepOrder` and renumbers to exactly this on save, so this
    is not bookkeeping: it is how the order of the exercises is expressed, and
    producing anything else would mean every run saw a difference and wrote
    again. A strength workout nests one level - a group holding steps - which
    is all this numbers.
    """
    order = 1
    child = 0
    for segment in payload.get("workoutSegments") or []:
        for step in segment.get("workoutSteps") or []:
            step["stepOrder"] = order
            order += 1

            children = step.get("workoutSteps")
            if not children:
                step["childStepId"] = None
                continue

            child += 1
            step["childStepId"] = child
            for inner in children:
                inner["stepOrder"] = order
                inner["childStepId"] = child
                order += 1


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
