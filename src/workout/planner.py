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
from .garmin.payloads import (
    GENERATED_NOTE,
    apply_note,
    apply_target,
    iter_workout_steps,
    step_category,
    step_exercise_name,
    step_note,
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
class Plan:
    """What a single workout would become."""

    workout: Workout
    payload: dict[str, Any]
    changes: list[Change]
    warnings: list[str]
    #: Exercises whose notes field was rewritten, which is its own reason to
    #: save a workout: editing workouts.yaml moves no target on its own.
    notes: list[str] = field(default_factory=list)

    @property
    def moved(self) -> list[Change]:
        return [change for change in self.changes if change.moved]

    @property
    def writable(self) -> bool:
        """Whether this plan has anything worth sending to Garmin."""
        return bool(self.moved or self.notes)


class ActivityNotFound(LookupError):
    """No activity matched any workout's prefixes."""


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


def plan_workout(
    workout: Workout, payload: dict[str, Any], performed: Performed
) -> Plan:
    """Work out the new target for every step of the workout just performed."""
    specs = index_specs(workout.exercises)

    changes: list[Change] = []
    warnings: list[str] = []
    notes: list[str] = []

    for step in iter_workout_steps(payload):
        label = step_exercise_name(step) or step_category(step)
        if not label:
            continue

        spec = _match(step, specs)
        if spec is None:
            warnings.append(f"{label}: not in workouts.yaml, skipped")
            continue

        # Before the target checks below: the note describes the programming,
        # so it belongs on the step whether or not this session moved anything.
        _refresh_note(step, spec, notes, warnings)

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

    return Plan(workout, payload, changes, warnings, notes)


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

    for step in iter_workout_steps(payload):
        spec = _match(step, specs)
        if spec is None:
            continue

        _refresh_note(step, spec, notes, warnings)

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

    return Plan(workout, payload, changes, warnings, notes)


def decided_targets(plan: Plan) -> dict[str, Target]:
    """The targets that moved, keyed for lookup in another workout."""
    return {normalise(c.spec.garmin_name): c.new for c in plan.moved}
