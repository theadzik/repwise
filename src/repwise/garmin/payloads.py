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

import re
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
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
#:
#: The trailing group is an exercise's own `notes`, written on the end of the
#: generated part. It has to be here: without it a note carrying a cue stops
#: looking like one of ours the moment it is written, and every later run reads
#: it as hand-typed - leaving it alone, warning about it, and quietly never
#: propagating a rep range or a step change again.
#:
#: The cost is that a cue typed into Connect *onto the end of a generated note*
#: is overwritten rather than protected. That is the right way round now that
#: the config carries cues of its own: `workouts.yaml` decides them, as it
#: decides everything else about a step. A note typed from scratch still does
#: not match, and is still left alone.
GENERATED_NOTE = re.compile(
    r"^\d+-\d+ (?:reps|s)(?: by \d+)? \| (?:bodyweight|\+[\d.]+ kg)(?: \| .*)?$"
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
    """One exercise, together with the repeat group or groups that hold it.

    A workout is a tree, and every caller needs the same two things out of it:
    the exercise step itself, and what the repeat group around it says - how
    many iterations it prescribes, and the rest step alongside it.

    An exercise whose sets do not all ask for the same figure is held as *two*
    adjacent groups, since a group repeats one step identically and cannot say
    two things at once. Above this module that is still one exercise, so both
    halves travel in one block and `sets` is the two counts added together.
    The singular properties address the first half, which is the harder one and
    the one whose name, category and note describe the whole exercise.

    Rest steps are kept rather than the seconds they hold, because the planner
    writes to them as well as reading them; `rest` stays honest about what the
    step currently says either way.
    """

    steps: list[dict[str, Any]]
    sets: int
    rest_steps: list[dict[str, Any]] = field(default_factory=list)
    groups: list[dict[str, Any]] = field(default_factory=list)

    @property
    def step(self) -> dict[str, Any]:
        """The exercise step that speaks for the block."""
        return self.steps[0]

    @property
    def rest_step(self) -> dict[str, Any] | None:
        """The rest between sets, or None when Garmin prescribes no interval."""
        return self.rest_steps[0] if self.rest_steps else None

    @property
    def group(self) -> dict[str, Any] | None:
        """The repeat group, or None for an exercise performed once."""
        return self.groups[0] if self.groups else None

    @property
    def rest(self) -> int | None:
        """Seconds between sets, or None when Garmin prescribes no interval."""
        return None if self.rest_step is None else step_rest(self.rest_step)

    @property
    def outer(self) -> dict[str, Any]:
        """The step this block occupies in the workout's own list.

        The repeat group when the exercise is performed for sets, and the
        exercise step itself when it stands alone. That is the thing to move
        when the exercise moves: everything else travels inside it.
        """
        return self.groups[0] if self.groups else self.steps[0]

    @property
    def outers(self) -> list[dict[str, Any]]:
        """Every step this block occupies, in order.

        Two when a ramp has this exercise split across a pair of groups. They
        move together and stay side by side: a rest between exercises must not
        come between the two halves of one.
        """
        return list(self.groups) if self.groups else [self.steps[0]]


def is_rest(step: dict[str, Any]) -> bool:
    """Whether this step is a rest rather than something to perform."""
    return (step.get("stepType") or {}).get("stepTypeKey") == "rest"


def is_timed_rest(step: dict[str, Any]) -> bool:
    """Whether a rest counts down, rather than waiting for the lap button.

    The duration is tested against None rather than for truth, as `step_target`
    tests its own: what makes a rest unreadable is ending on the lap button or
    carrying no value at all. Zero seconds is a duration like any other, and
    treating it as absent would refuse to write a configured rest onto a step
    that can hold one perfectly well.
    """
    end = step.get("endCondition") or {}
    return (
        end.get("conditionTypeKey") == "time"
        and step.get("endConditionValue") is not None
    )


def exercise_step(group: dict[str, Any]) -> dict[str, Any]:
    """The exercise inside a repeat group, or the step itself when it is bare."""
    for inner in group.get("workoutSteps") or []:
        if inner.get("exerciseName") or inner.get("category"):
            return inner
    return group


def _iterations(group: dict[str, Any]) -> int:
    return int(group.get("numberOfIterations") or 1)


def _rest_step(steps: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The rest step of a repeat group, when it prescribes a fixed time."""
    for step in steps:
        # A lap.button rest has no duration, only a prompt to press the button.
        if is_rest(step) and is_timed_rest(step):
            return step
    return None


def _same_exercise(one: dict[str, Any], other: dict[str, Any]) -> bool:
    """Whether two steps are two halves of the same exercise.

    The name settles it when both carry one; failing that, the category does.
    Deliberately stricter than `domain/matching.py`: this is not "which spec
    does this belong to" but "is this literally the step next door split in
    two", and only something this tool wrote that way should merge.
    """
    name, other_name = step_exercise_name(one), step_exercise_name(other)
    if name and other_name:
        return normalise(name) == normalise(other_name)

    category, other_category = step_category(one), step_category(other)
    return bool(category and other_category and category == other_category)


def _walk_groups(workout: dict[str, Any]) -> Iterator[ExerciseBlock]:
    """One block per repeat group, before adjacent halves are put together."""

    def walk(steps: list[dict[str, Any]] | None) -> Iterator[ExerciseBlock]:
        for step in steps or []:
            children = step.get("workoutSteps")
            if children:
                sets = int(step.get("numberOfIterations") or 1)
                rest = _rest_step(children)
                for inner in walk(children):
                    # An inner repeat group keeps its own count and rest, and
                    # is the group its exercise travels with.
                    yield ExerciseBlock(
                        inner.steps,
                        inner.sets if inner.sets > 1 else sets,
                        inner.rest_steps or ([rest] if rest is not None else []),
                        inner.groups or [step],
                    )
            elif step.get("exerciseName") or step.get("category"):
                yield ExerciseBlock([step], 1)

    for segment in workout.get("workoutSegments") or []:
        yield from walk(segment.get("workoutSteps"))


def iter_exercise_blocks(workout: dict[str, Any]) -> Iterator[ExerciseBlock]:
    """Yield each exercise with its set count and rest step.

    Sets are modelled as a RepeatGroupDTO wrapping one executable step plus a
    rest step, so a workout holds one step per exercise, not one per set. The
    rest steps and any step with neither a name nor a category are skipped:
    what comes out is one block per exercise, in the order they are performed.

    Two *adjacent* groups naming the same exercise are its two halves - the
    leading sets asking for one rep more than the rest - and are yielded as a
    single block whose `sets` is the two counts added together. Merging here is
    what lets everything above this module carry on thinking in exercises: the
    planner matches one spec to one block, and the checker cannot see the same
    name twice and call it ambiguous.
    """
    merged: list[ExerciseBlock] = []

    for block in _walk_groups(workout):
        last = merged[-1] if merged else None
        if (
            last is not None
            and last.groups
            and block.groups
            and _same_exercise(last.step, block.step)
        ):
            merged[-1] = ExerciseBlock(
                last.steps + block.steps,
                last.sets + block.sets,
                last.rest_steps + block.rest_steps,
                last.groups + block.groups,
            )
            continue
        merged.append(block)

    yield from merged


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


def block_target(block: ExerciseBlock, spec: ExerciseSpec) -> Target | None:
    """The prescription an exercise currently holds, across all of its sets.

    One group is a flat target and reads straight off its step. Two are a ramp:
    the base is the lower figure, and `lead` is how many sets ask for the
    higher one. Read by value rather than by position, so a pair reordered by
    hand in Connect still reads as the ramp it is.

    A pair that is not one step apart is not a ramp this tool could have
    written, so it reads as its own base with no lead, and the next target
    written over it collapses the split.
    """
    targets = [step_target(step, spec.time_based) for step in block.steps]
    if not targets or any(target is None for target in targets):
        return None

    found = [target for target in targets if target is not None]
    base = min(target.reps for target in found)
    higher = base + spec.rep_step
    lead = sum(
        _iterations(group)
        for group, target in zip(block.groups, found, strict=False)
        if target.reps == higher
    )
    return Target(base, found[0].weight, lead)


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


def apply_sets(group: dict[str, Any], sets: int) -> None:
    """Write a new set count onto a repeat group, in place.

    Garmin holds the number twice - as the group's iterations, and as the value
    its `iterations` end condition counts up to - and the two have to agree.
    """
    group["numberOfIterations"] = sets
    group["endConditionValue"] = float(sets)


def skips_last_rest(group: dict[str, Any]) -> bool:
    """Whether a repeat group drops the rest that follows its final set.

    Connect's own switch, set per group and stored nowhere else. Absent and
    null both read as not skipping, which is how Garmin treats them: only the
    groups Connect has had the switch turned on for carry it as true.
    """
    return bool(group.get("skipLastRestStep"))


def apply_last_rest(group: dict[str, Any]) -> None:
    """Make a repeat group rest after its final set, in place.

    Written as a value rather than by removing the key, so that a group Garmin
    returned without one says what it means once it comes back.
    """
    group["skipLastRestStep"] = False


def apply_rest(step: dict[str, Any], seconds: int) -> None:
    """Write an interval onto a rest step, in place.

    The end condition is written along with the duration, so that a rest which
    waited for the lap button becomes one that counts down. Whether that is a
    change worth making is the planner's to judge, not this module's: here it
    is simply what "rest for this long" means.
    """
    step["endCondition"] = dict(END_TIME)
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


def _rungs(spec: ExerciseSpec, target: Target) -> list[tuple[int, int]]:
    """The target as (how many sets, asking for how many reps), hardest first.

    A run-length encoding of what each set is asked for, which for a flat
    target is one rung and for a ramp is two. Written this way round because it
    is exactly the shape a repeat group holds.
    """
    rungs: list[tuple[int, int]] = []
    for reps in target.per_set(spec.sets, spec.rep_step):
        if rungs and rungs[-1][1] == reps:
            count, figure = rungs[-1]
            rungs[-1] = (count + 1, figure)
        else:
            rungs.append((1, reps))
    return rungs


def apply_block(
    block: ExerciseBlock, spec: ExerciseSpec, target: Target
) -> list[dict[str, Any]]:
    """Write a target across an exercise's sets, and say what steps it now needs.

    A flat target is one repeat group, as it has always been. A ramped one is
    two adjacent groups - the leading sets, then the rest - because a group
    repeats a single step identically and cannot say two things at once.

    Groups Garmin already holds are reused and rewritten rather than rebuilt,
    so an exercise keeps its identity across a change of shape; a half that is
    no longer needed is dropped, and one that is missing is built.

    The steps are returned rather than spliced into the workout, because where
    they sit is the caller's business - and because the two halves have to stay
    side by side, which is a fact about laying out a workout rather than about
    this exercise.
    """
    if not block.groups:
        # Performed once, with no group to count sets: there is nowhere to put
        # a second figure, so write the base and leave the shape alone.
        apply_target(block.step, target)
        return [block.step]

    rungs = _rungs(spec, target)
    groups = list(block.groups)
    while len(groups) < len(rungs):
        groups.append(new_group(spec, target))
    del groups[len(rungs) :]

    for group, (sets, reps) in zip(groups, rungs, strict=True):
        apply_sets(group, sets)
        apply_target(exercise_step(group), Target(reps, target.weight))

    return groups


def steps_between(
    payload: dict[str, Any], blocks: list[ExerciseBlock]
) -> list[dict[str, Any]]:
    """The top-level steps belonging to no exercise: rests, and anything else.

    Kept and put back rather than rebuilt, so that a run which changes nothing
    leaves the between-exercise rests exactly as Garmin stored them - down to
    the value a lap-button rest carries and ignores.
    """
    exercises = {id(outer) for block in blocks for outer in block.outers}
    segments = payload.get("workoutSegments") or [{}]
    return [
        step
        for step in segments[0].get("workoutSteps") or []
        if id(step) not in exercises
    ]


def set_exercise_steps(
    payload: dict[str, Any],
    exercises: list[list[dict[str, Any]]],
    gaps: list[dict[str, Any]],
) -> None:
    """Lay out exercise, gap, exercise ... as the workout's steps, and renumber.

    One gap per join, so nothing follows the last exercise: a workout ends when
    its last set does. Each gap must be a step of its own - they are numbered
    individually, so the same dict passed twice would leave two positions
    claimed by one step.

    An exercise is a list of steps rather than one step, because a ramp splits
    it across two groups. They are laid down together with no gap between them:
    the first group's own rest step already covers the pause between its last
    set and the next one, and a second rest there would be one the config never
    asked for.

    Steps are placed as given rather than copied, so a group taken out of a
    fetched workout keeps its identity, its ids, and the target stored in it.
    """
    steps: list[dict[str, Any]] = []
    for position, group in enumerate(exercises):
        if position:
            steps.append(gaps[position - 1])
        steps.extend(group)

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


# --- the workout an activity executed ---------------------------------------
#
# A third payload, beside the workout definition and the logged sets: the
# workout as the watch actually ran it, kept with the activity. It is the only
# record of what a past session was asked for, because the definition stored in
# Garmin holds the target for the *next* session - `update` rewrote it after
# that one finished.
#
# It expresses sets the way FIT does rather than the way a workout definition
# does: no nesting, and a repeat step at the *end* of the run it repeats,
# saying which step to jump back to and how many times. It carries no weight,
# which is a gap in Garmin's JSON rather than in the record - the activity's
# own FIT file has it - and nothing here needs one.

REPEAT_UNTIL_DONE = "REPEAT_UNTIL_STEPS_CMPLT"


@dataclass(frozen=True)
class ExecutedExercise:
    """One exercise of a workout as an activity executed it."""

    name: str | None
    category: str | None
    time_based: bool
    #: What each of its sets was asked for.
    reps: list[int]


def executed_exercises(snapshot: list[dict[str, Any]]) -> list[ExecutedExercise]:
    """The exercises a past activity was performed against, in order.

    One entry per repeat group, so a ramped exercise arrives as two that the
    caller joins back together - the same shape `iter_exercise_blocks` yields
    for a stored workout, and merged the same way.
    """
    if not snapshot:
        return []
    steps = snapshot[0].get("steps") or []

    # A repeat step jumps back to `durationValue` and runs `targetValue` times,
    # so it says how many sets every step from there up to itself is performed
    # for. Unrolled into a lookup, since the steps are read in their own order.
    repeats: dict[int, int] = {}
    for step in steps:
        if step.get("durationType") != REPEAT_UNTIL_DONE:
            continue
        start = int(step.get("durationValue") or 0)
        for index in range(start, int(step.get("stepIndex") or 0)):
            repeats[index] = int(step.get("targetValue") or 1)

    found: list[ExecutedExercise] = []
    for step in steps:
        if step.get("intensity") != "ACTIVE":
            continue  # rest steps, and the repeat markers themselves
        name = step.get("exerciseName")
        category = step.get("exerciseCategory")
        if not name and not category:
            continue

        kind = step.get("durationType")
        if kind not in ("REPS", "TIME"):
            continue  # ended on the lap button or on nothing we can read
        asked = int(step.get("durationValue") or 0)
        sets = repeats.get(int(step.get("stepIndex") or 0), 1)
        found.append(ExecutedExercise(name, category, kind == "TIME", [asked] * sets))

    return found


# --- logged activities -----------------------------------------------------


def activity_sport(activity: dict[str, Any]) -> str | None:
    """What sport a performed activity was, as a list of them reports it.

    Three payloads spell this three ways for the same answer: a workout says
    `sportType.sportTypeKey`, an activity in a list says `activityType.typeKey`,
    and that same activity fetched on its own says `activityTypeDTO.typeKey`.
    The list shape is the one read here, because listing is where the question
    gets asked - filtering a scan before deciding what to download.
    """
    return (activity.get("activityType") or {}).get("typeKey")


def performed_sets(
    sets_payload: dict[str, Any],
) -> tuple[dict[str, list[PerformedSet]], dict[str, list[PerformedSet]]]:
    """Index the working sets by exercise name and, separately, by category.

    Weights arrive in grams, and a set edited in Connect without one comes back
    as -1 instead of null or zero. That is Garmin's way of saying "no figure",
    not a load - but nothing downstream can tell the difference, because -1 g
    is -0.001 kg and that reads as a weight like any other. The rules then take
    it for the load you trained at and prescribe the next target at a negative
    weight. So "no weight recorded" and "no weight used" are read the same way
    here: 0 kg.
    """
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

        grams = max(float(entry.get("weight") or 0.0), 0.0)
        logged = PerformedSet(
            int(reps),
            round(grams / GRAMS_PER_KG, 3),
            float(entry.get("duration") or 0.0),
        )

        name = exercises[0].get("name")
        category = exercises[0].get("category")
        if name:
            by_name[normalise(name)].append(logged)
        if category:
            by_category[normalise(category)].append(logged)

    return dict(by_name), dict(by_category)
