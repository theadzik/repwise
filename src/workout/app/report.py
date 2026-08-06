"""How a change, a plan and a finding are printed.

Report lines go out through the standard library logger, the way `log.py`
describes: INFO is the report the user asked for and lands on stdout, WARNING
and above are problems and land on stderr. A use case therefore emits its
report without knowing where the report goes - only `main()` decides that.
"""

from __future__ import annotations

import logging

from ..domain.models import ExerciseSpec
from ..domain.progression import Target
from ..planner import (
    Change,
    GapChange,
    Plan,
    RestChange,
    SetChange,
    StructureChange,
)

logger = logging.getLogger(__name__)

#: How `check` shows a finding: the marker that survives a plain run, and the
#: level it is logged at, so severity outlives a redirect of stdout too. Both
#: levels are things to go and fix - an error stops the exercise working at
#: all, a warning means it works by luck - and both fail the command.
SEVERITY = {
    "error": ("!!", logging.ERROR),
    "warning": (" !", logging.WARNING),
}


def describe(spec: ExerciseSpec, target: Target) -> str:
    """Render a target the way the exercise is actually measured."""
    if spec.time_based:
        return f"{target.reps} s"
    if spec.bodyweight:
        return f"{target.reps} reps"
    return f"{target.reps} x {target.weight:g} kg"


def report_change(change: Change, force_flag: str | None = None) -> None:
    flag = force_flag or ("*" if change.moved else " ")
    logger.info(
        f"{flag} {change.spec.name:<40}"
        f" {describe(change.spec, change.old):>13}"
        f"  ->  {describe(change.spec, change.new):<13} ({change.reason})"
    )


def report_prescribed(name: str, old: str, new: str, source: str) -> None:
    """A number workouts.yaml moved, in the same columns as a target.

    Shown like a target rather than hidden like a note: these change how the
    workout is performed, and are on the watch from the next sync.
    """
    logger.info(f"* {name:<40} {old:>13}  ->  {new:<13} ({source})")


def report_rest(change: RestChange) -> None:
    report_prescribed(
        change.spec.name,
        f"{change.old} s rest",
        f"{change.new} s rest",
        "rest from workouts.yaml",
    )


def report_sets(change: SetChange) -> None:
    report_prescribed(
        change.spec.name,
        f"{change.old} sets",
        f"{change.new} sets",
        "sets from workouts.yaml",
    )


def report_gaps(change: GapChange) -> None:
    """The rest between exercises: one line for the workout, not one per gap."""
    report_prescribed(
        "Between exercises",
        change.before,
        f"{change.new} s rest",
        f"{change.gaps} gap(s), from workouts.yaml",
    )


#: The marker each kind of structural change prints under. Deliberately not
#: `*`: these change what the workout is, not what it asks of you.
STRUCTURE = {"added": "+", "removed": "-", "moved": "~"}


def report_structure(change: StructureChange) -> None:
    """An exercise the config added, dropped, or put somewhere else."""
    if change.kind == "added" and change.spec and change.target:
        detail = (
            f"new at position {change.position}, "
            f"{change.spec.sets} x {describe(change.spec, change.target)}"
        )
    elif change.kind == "removed":
        detail = "removed: no longer in workouts.yaml"
    else:
        detail = f"moved to position {change.position}"

    logger.info(f"{STRUCTURE.get(change.kind, ' ')} {change.name:<40} {detail}")


def report_plan(plan: Plan, force_flag: str | None = None) -> None:
    # Structure first: what a workout holds has to make sense before what each
    # of its exercises is asking for does.
    for shape in plan.structure:
        report_structure(shape)
    for change in plan.changes:
        report_change(change, force_flag)
    for count in plan.sets:
        report_sets(count)
    for rest in plan.rests:
        report_rest(rest)
    if plan.gaps:
        report_gaps(plan.gaps)
    # Notes only move when workouts.yaml does, so they are a footnote to a
    # normal run: the count goes in the summary, the detail behind -v.
    for name in plan.notes:
        logger.debug(f"  note {name:<38} -> {plan.workout.key}")
    for warning in plan.warnings:
        # The marker survives the move to logging: it still sets a warning
        # apart when the level itself is not shown.
        logger.warning(f"  ! {warning}")
