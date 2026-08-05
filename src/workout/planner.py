"""Decide what should change, without changing anything yet.

The planner matches Garmin's workout steps to the exercises declared in
workouts.yaml, runs the progression rules over them, and reports the result.
It mutates the workout payload it is handed but performs no I/O, so a caller
can inspect a plan and discard it -- which is what a dry run does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .domain.matching import ExerciseIndex, normalise
from .domain.models import Config, ExerciseSpec, Workout
from .domain.progression import PerformedSet, Target, next_target
from .errors import ActivityNotFound
from .garmin.payloads import (
    GENERATED_NOTE,
    ExerciseBlock,
    apply_note,
    apply_rest,
    apply_sets,
    apply_target,
    iter_exercise_blocks,
    new_group,
    new_rest,
    set_exercise_steps,
    step_category,
    step_exercise_name,
    step_note,
    step_rest,
    step_target,
)

Performed = tuple[dict[str, list[PerformedSet]], dict[str, list[PerformedSet]]]


@dataclass(frozen=True)
class Change:
    """One exercise's before and after, and why."""

    spec: ExerciseSpec
    old: Target
    new: Target
    reason: str

    @property
    def moved(self) -> bool:
        return self.old != self.new


@dataclass(frozen=True)
class RestChange:
    """One exercise's rest between sets, before and after.

    Not a `Change`: a rest is prescribed by workouts.yaml rather than earned in
    a session, so there is no reason to report for it, and nothing to record at
    all unless it moved.
    """

    spec: ExerciseSpec
    old: int
    new: int


@dataclass(frozen=True)
class SetChange:
    """One exercise's set count, before and after.

    Prescribed by workouts.yaml rather than earned, exactly as a rest is.
    """

    spec: ExerciseSpec
    old: int
    new: int


@dataclass(frozen=True)
class StructureChange:
    """An exercise added to a workout, removed from it, or moved within it.

    The shape of a workout is the config's to decide, so none of these are
    earned in a session: they follow from the file being edited.
    """

    kind: str
    #: What to call it. The configured name for an exercise the config knows,
    #: and whatever Garmin calls it for one being removed.
    name: str
    #: Where it now sits among the exercises, counting from 1. None when it is
    #: being removed and so sits nowhere.
    position: int | None = None
    #: What a newly built step starts at. Only set when kind is "added".
    spec: ExerciseSpec | None = None
    target: Target | None = None


@dataclass(frozen=True)
class Plan:
    """What a single workout would become."""

    workout: Workout
    payload: dict[str, Any]
    changes: list[Change]
    warnings: list[str]
    #: Exercises whose notes field was rewritten, which is its own reason to
    #: save a workout: editing workouts.yaml moves no target on its own.
    notes: list[str] = field(default_factory=list)
    #: Exercises whose rest step was rewritten. Config-driven like the notes,
    #: and a reason to save for the same reason.
    rests: list[RestChange] = field(default_factory=list)
    #: Exercises whose repeat group now prescribes a different number of sets.
    sets: list[SetChange] = field(default_factory=list)
    #: Exercises added, removed or moved. Config-driven again, and the only
    #: kind of change that alters what the workout is rather than what it asks.
    structure: list[StructureChange] = field(default_factory=list)

    @property
    def moved(self) -> list[Change]:
        return [change for change in self.changes if change.moved]

    @property
    def writable(self) -> bool:
        """Whether this plan has anything worth sending to Garmin."""
        return bool(
            self.moved or self.notes or self.rests or self.sets or self.structure
        )


def find_workout(config: Config, activity_name: str) -> Workout:
    """Which workout does an activity belong to, by name prefix."""
    name = activity_name.lower()
    for workout in config:
        if any(name.startswith(prefix) for prefix in workout.activity_prefixes):
            return workout
    raise ActivityNotFound(f"Cannot tell which workout '{activity_name}' belongs to.")


def index_specs(exercises: list[ExerciseSpec]) -> ExerciseIndex[ExerciseSpec]:
    """Index a workout's exercises for lookup from a payload.

    `garmin_name` is what Garmin calls the movement, so it is authoritative;
    the friendly `name` is an alias, which is what lets a step named either way
    find its spec.
    """
    index: ExerciseIndex[ExerciseSpec] = ExerciseIndex()
    for spec in exercises:
        index.add(
            spec,
            name=spec.garmin_name,
            aliases=(spec.name,),
            category=spec.garmin_category,
        )
    return index


def _match(
    step: dict[str, Any], specs: ExerciseIndex[ExerciseSpec]
) -> ExerciseSpec | None:
    """Find the spec for a workout step: name first, then category."""
    return specs.find(step_exercise_name(step), step_category(step))


def _logged_for(
    spec: ExerciseSpec, step: dict[str, Any], performed: Performed
) -> list[PerformedSet]:
    """Sets logged for an exercise, tolerating the name Garmin chose."""
    by_name, by_category = performed
    candidates = [
        by_name.get(normalise(step_exercise_name(step) or "")),
        by_name.get(normalise(spec.garmin_name)),
        by_category.get(normalise(spec.garmin_category))
        if spec.garmin_category
        else None,
    ]
    for logged in candidates:
        if logged:
            return logged
    return []


def _refresh_note(
    step: dict[str, Any],
    spec: ExerciseSpec,
    notes: list[str],
    warnings: list[str],
) -> None:
    """Keep the step's notes field showing how the exercise is programmed.

    Only a blank note, or one this tool wrote before, is replaced. Anything
    else is a cue the user typed into Garmin Connect, and overwriting it would
    destroy it silently, so it is reported and left alone instead.
    """
    wanted = spec.note
    current = step_note(step)
    if current == wanted:
        return
    if current and not GENERATED_NOTE.match(current):
        warnings.append(
            f"{spec.name}: has its own note, left alone (wanted {wanted!r})"
        )
        return
    apply_note(step, wanted)
    notes.append(spec.name)


def _refresh_rest(
    block: ExerciseBlock,
    spec: ExerciseSpec,
    rests: list[RestChange],
    warnings: list[str],
) -> None:
    """Keep the repeat group's rest step showing the configured interval.

    workouts.yaml is the source of the programming, so a `rest` declared there
    is written the way a note is. An exercise that declares none has no opinion
    and its step is left alone, which is also what keeps a config that predates
    this behaviour writing nothing.

    Only a rest Garmin stores as a fixed time can be written. A lap.button rest
    is a prompt to press the button rather than an interval, and turning one
    into a countdown would change how the workout is performed rather than
    correct a value, so it is reported and left alone.
    """
    if not spec.rest:
        return

    rest_step = block.rest_step
    if rest_step is None:
        warnings.append(
            f"{spec.name}: rest is not a fixed time in Garmin, left alone "
            f"(wanted {spec.rest}s)"
        )
        return

    current = step_rest(rest_step)
    if current == spec.rest:
        return

    apply_rest(rest_step, spec.rest)
    rests.append(RestChange(spec, current, spec.rest))


def _refresh_sets(
    block: ExerciseBlock,
    spec: ExerciseSpec,
    sets: list[SetChange],
    warnings: list[str],
) -> None:
    """Keep the repeat group prescribing as many sets as the config asks for.

    Garmin counts sets as the iterations of the group around an exercise, so an
    exercise it performs once may have no group at all. Building one around a
    step Garmin already holds is a change of shape rather than of number, and
    is left to Connect: the step is reported and kept as it is.
    """
    if block.sets == spec.sets:
        return

    if block.group is None:
        warnings.append(
            f"{spec.name}: {spec.sets} sets in config, but Garmin performs it "
            f"once, with no repeat group to count them"
        )
        return

    apply_sets(block.group, spec.sets)
    sets.append(SetChange(spec, block.sets, spec.sets))


def _index_blocks(blocks: list[ExerciseBlock]) -> ExerciseIndex[ExerciseBlock]:
    """The workout's exercises, looked up the way its steps are matched."""
    index: ExerciseIndex[ExerciseBlock] = ExerciseIndex()
    for block in blocks:
        index.add(
            block,
            name=step_exercise_name(block.step),
            category=step_category(block.step),
        )
    return index


def _existing_gaps(
    payload: dict[str, Any], blocks: list[ExerciseBlock]
) -> list[dict[str, Any]]:
    """The steps between the exercises: rests, and anything else up there.

    Kept and put back rather than rebuilt, so that a run which changes nothing
    leaves the between-exercise rests exactly as Garmin stored them - down to
    the value a lap-button rest carries and ignores.
    """
    exercises = {id(block.outer) for block in blocks}
    segments = payload.get("workoutSegments") or [{}]
    return [
        step
        for step in segments[0].get("workoutSteps") or []
        if id(step) not in exercises
    ]


def _reconcile(
    workout: Workout,
    payload: dict[str, Any],
    structure: list[StructureChange],
    added: set[int],
) -> None:
    """Make the workout hold the exercises workouts.yaml names, in that order.

    Steps Garmin already has are moved rather than rebuilt: the target lives in
    the step and nowhere else, so a rebuilt one would silently restart the
    progression. An exercise the config names but Garmin lacks is built at the
    bottom of its range; one Garmin has but the config no longer names is
    dropped, which is the config being the source of truth taken seriously.

    Nothing is decided about order beyond what the file says: `set_exercise_steps`
    renumbers, and Garmin sorts by those numbers.
    """
    blocks = list(iter_exercise_blocks(payload))
    index = _index_blocks(blocks)
    gaps = _existing_gaps(payload, blocks)

    # Steps are compared by identity throughout: two exercises can hold equal
    # dictionaries, and it matters which one of them is being moved.
    was = {id(block.outer): position for position, block in enumerate(blocks)}
    labels = {
        id(block.outer): step_exercise_name(block.step)
        or step_category(block.step)
        or "?"
        for block in blocks
    }

    outers: list[dict[str, Any]] = []
    kept: list[int] = []

    for spec in workout.exercises:
        block = index.find(spec.garmin_name, spec.garmin_category)
        if block is None or id(block.outer) in kept:
            # Nothing in Garmin answers to this, or an earlier exercise already
            # claimed the step that does. Either way it needs one of its own.
            target = Target(spec.rep_low, spec.start_weight)
            group = new_group(spec, target)
            outers.append(group)
            added.add(id(group))
            structure.append(
                StructureChange("added", spec.name, len(outers), spec, target)
            )
            continue

        outers.append(block.outer)
        kept.append(id(block.outer))
        # Now that a spec claims it, call it what the config calls it. Only an
        # exercise being removed keeps the name Garmin knows it by, there being
        # nothing else left to call it.
        labels[id(block.outer)] = spec.name

    for block in blocks:
        if id(block.outer) not in kept:
            structure.append(StructureChange("removed", labels[id(block.outer)]))

    at = {id(outer): position for position, outer in enumerate(outers)}
    for ident in _out_of_order(kept, was):
        structure.append(StructureChange("moved", labels[ident], at[ident] + 1))

    if structure:
        set_exercise_steps(payload, outers, _gaps_for(outers, gaps))


def _out_of_order(kept: list[int], was: dict[int, int]) -> list[int]:
    """The fewest exercises whose moving accounts for the new order.

    Everything that held its relative place is left out of the report, and what
    is left is the complement of the longest such run. Without this, moving the
    plank from last to first reads as every other exercise moving down one -
    true of their positions, and useless to read.

    Longest increasing subsequence, quadratic, over a handful of exercises.
    """
    if not kept:
        return []

    before = [was[ident] for ident in kept]
    longest = [1] * len(before)
    came_from = [-1] * len(before)
    for later in range(len(before)):
        for earlier in range(later):
            if (
                before[earlier] < before[later]
                and longest[earlier] + 1 > longest[later]
            ):
                longest[later] = longest[earlier] + 1
                came_from[later] = earlier

    at = max(range(len(before)), key=lambda position: longest[position])
    in_place = set()
    while at != -1:
        in_place.add(at)
        at = came_from[at]

    return [ident for position, ident in enumerate(kept) if position not in in_place]


def _exercise_step(outer: dict[str, Any]) -> dict[str, Any]:
    """The exercise inside a repeat group, or the step itself when bare."""
    for inner in outer.get("workoutSteps") or []:
        if inner.get("exerciseName") or inner.get("category"):
            return inner
    return outer


def _gaps_for(
    outers: list[dict[str, Any]], existing: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """One step per join, reusing what was there and building the shortfall."""
    needed = max(len(outers) - 1, 0)
    gaps = existing[:needed]
    return gaps + [new_rest(None) for _ in range(needed - len(gaps))]


def plan_workout(
    workout: Workout, payload: dict[str, Any], performed: Performed | None = None
) -> Plan:
    """Bring a workout in line with the config, and advance what was trained.

    Two kinds of change, in that order. The config decides the shape - which
    exercises, in what order, resting how long, described how - and is applied
    whether or not anything was trained. The session decides the targets, and
    only for the exercises it actually contains: pass no `performed` at all for
    a workout with no session behind it, and only the first kind happens.
    """
    specs = index_specs(workout.exercises)

    changes: list[Change] = []
    warnings: list[str] = []
    notes: list[str] = []
    rests: list[RestChange] = []
    sets: list[SetChange] = []
    structure: list[StructureChange] = []
    added: set[int] = set()

    _reconcile(workout, payload, structure, added)

    for block in iter_exercise_blocks(payload):
        step = block.step
        label = step_exercise_name(step) or step_category(step)

        spec = _match(step, specs)
        if spec is None:
            # Unreachable after reconciling, which keeps only what the config
            # names, but a step that matched nothing is not one to write to.
            warnings.append(f"{label}: not in workouts.yaml, skipped")
            continue

        # Before the target checks below: the note, the rest and the set count
        # describe the programming, so they belong on the step whether or not
        # this session moved anything.
        _refresh_note(step, spec, notes, warnings)
        _refresh_rest(block, spec, rests, warnings)
        _refresh_sets(block, spec, sets, warnings)

        if performed is None or id(block.outer) in added:
            # Either no session to learn from, or a step this run has just
            # built, which already holds exactly what the config asks for.
            continue

        current = step_target(step, spec.time_based)
        if current is None:
            kind = "time" if spec.time_based else "rep"
            warnings.append(f"{label}: step has no {kind} target, skipped")
            continue

        logged = _logged_for(spec, step, performed)
        if not logged:
            warnings.append(f"{spec.name}: not found in the activity, skipped")
            continue

        if spec.time_based:
            # Garmin logs a hold as 1 rep; the duration is the real figure.
            logged = [entry.as_time() for entry in logged]

        new, why = next_target(spec, current, logged)
        change = Change(spec, current, new, why)
        changes.append(change)
        if change.moved:
            apply_target(step, new)

    return Plan(
        workout,
        payload,
        changes,
        warnings,
        notes,
        rests,
        sets=sets,
        structure=structure,
    )


def plan_sync(
    workout: Workout,
    payload: dict[str, Any],
    targets: dict[str, Target],
    source: str,
) -> Plan:
    """Force already-decided targets onto another workout's matching steps.

    An exercise can appear in more than one workout -- the calf raise is in
    both -- and a target earned in one session should hold everywhere it
    appears, otherwise the copies drift apart.
    """
    specs = index_specs(workout.exercises)

    changes: list[Change] = []
    warnings: list[str] = []
    notes: list[str] = []
    rests: list[RestChange] = []
    sets: list[SetChange] = []

    for block in iter_exercise_blocks(payload):
        step = block.step
        spec = _match(step, specs)
        if spec is None:
            continue

        _refresh_note(step, spec, notes, warnings)
        _refresh_rest(block, spec, rests, warnings)
        _refresh_sets(block, spec, sets, warnings)

        new = targets.get(normalise(spec.garmin_name))
        if new is None:
            continue

        current = step_target(step, spec.time_based)
        if current is None or current == new:
            continue

        # load_config rejects mismatched ranges, but a hand-edited Garmin
        # workout can still be out of step, so say so rather than hide it.
        if not spec.rep_low <= new.reps <= spec.rep_high:
            warnings.append(
                f"{spec.name}: synced target {new.reps} is outside this "
                f"workout's {spec.rep_low}-{spec.rep_high} range"
            )

        apply_target(step, new)
        changes.append(Change(spec, current, new, f"synced from {source}"))

    return Plan(workout, payload, changes, warnings, notes, rests, sets=sets)


def decided_targets(plan: Plan) -> dict[str, Target]:
    """The targets that moved, keyed for lookup in another workout."""
    return {normalise(c.spec.garmin_name): c.new for c in plan.moved}
