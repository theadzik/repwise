"""Decide what should change, without changing anything yet.

The planner matches Garmin's workout steps to the exercises declared in
workouts.yaml, runs the progression rules over them, and reports the result.
It mutates the workout payload it is handed but performs no I/O, so a caller
can inspect a plan and discard it -- which is what a dry run does.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from .garmin.payloads import (
    apply_target,
    iter_workout_steps,
    normalise,
    step_category,
    step_exercise_name,
    step_target,
)
from .models import Config, ExerciseSpec, Workout
from .progression import PerformedSet, Target, next_target

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

    @property
    def moved(self) -> list[Change]:
        return [change for change in self.changes if change.moved]


class ActivityNotFound(LookupError):
    """No activity matched any workout's prefixes."""


def find_workout(config: Config, activity_name: str) -> Workout:
    """Which workout does an activity belong to, by name prefix."""
    name = activity_name.lower()
    for workout in config:
        if any(name.startswith(prefix) for prefix in workout.activity_prefixes):
            return workout
    raise ActivityNotFound(f"Cannot tell which workout '{activity_name}' belongs to.")


def index_specs(
    exercises: list[ExerciseSpec],
) -> tuple[dict[str, ExerciseSpec], dict[str, ExerciseSpec]]:
    """Index specs by name and by category.

    A category is only usable when exactly one exercise in the workout claims
    it, otherwise it could not say which one a set belongs to.
    """
    by_name: dict[str, ExerciseSpec] = {}
    for spec in exercises:
        by_name.setdefault(normalise(spec.name), spec)
        by_name[normalise(spec.garmin_name)] = spec  # authoritative, so overwrite

    claimed: dict[str, list[ExerciseSpec]] = defaultdict(list)
    for spec in exercises:
        if spec.garmin_category:
            claimed[normalise(spec.garmin_category)].append(spec)
    by_category = {key: specs[0] for key, specs in claimed.items() if len(specs) == 1}

    return by_name, by_category


def _match(
    step: dict[str, Any],
    by_name: dict[str, ExerciseSpec],
    by_category: dict[str, ExerciseSpec],
) -> ExerciseSpec | None:
    """Find the spec for a workout step: name first, then category."""
    spec = by_name.get(normalise(step_exercise_name(step) or ""))
    if spec is not None:
        return spec
    category = step_category(step)
    return by_category.get(normalise(category)) if category else None


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


def plan_workout(
    workout: Workout, payload: dict[str, Any], performed: Performed
) -> Plan:
    """Work out the new target for every step of the workout just performed."""
    by_name, by_category = index_specs(workout.exercises)

    changes: list[Change] = []
    warnings: list[str] = []

    for step in iter_workout_steps(payload):
        label = step_exercise_name(step) or step_category(step)
        if not label:
            continue

        spec = _match(step, by_name, by_category)
        if spec is None:
            warnings.append(f"{label}: not in workouts.yaml, skipped")
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

    return Plan(workout, payload, changes, warnings)


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
    by_name, by_category = index_specs(workout.exercises)

    changes: list[Change] = []
    warnings: list[str] = []

    for step in iter_workout_steps(payload):
        spec = _match(step, by_name, by_category)
        if spec is None:
            continue

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

    return Plan(workout, payload, changes, warnings)


def decided_targets(plan: Plan) -> dict[str, Target]:
    """The targets that moved, keyed for lookup in another workout."""
    return {normalise(c.spec.garmin_name): c.new for c in plan.moved}
