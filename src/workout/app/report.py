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
from ..planner import Change, Plan, RestChange

logger = logging.getLogger(__name__)

#: How `check` shows a finding: the marker that survives a plain run, and the
#: level it is logged at, so severity outlives a redirect of stdout too.
SEVERITY = {
    "error": ("!!", logging.ERROR),
    "warning": (" !", logging.WARNING),
    "note": ("  ", logging.INFO),
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


def report_rest(change: RestChange) -> None:
    """A rest workouts.yaml moved, in the same columns as a target.

    Shown like a target rather than hidden like a note: it changes how the
    workout is performed, and it is on the watch from the next sync.
    """
    logger.info(
        f"* {change.spec.name:<40}"
        f" {f'{change.old} s rest':>13}  ->  {f'{change.new} s rest':<13}"
        f" (rest from workouts.yaml)"
    )


def report_plan(plan: Plan, force_flag: str | None = None) -> None:
    for change in plan.changes:
        report_change(change, force_flag)
    for rest in plan.rests:
        report_rest(rest)
    # Notes only move when workouts.yaml does, so they are a footnote to a
    # normal run: the count goes in the summary, the detail behind -v.
    for name in plan.notes:
        logger.debug(f"  note {name:<38} -> {plan.workout.key}")
    for warning in plan.warnings:
        # The marker survives the move to logging: it still sets a warning
        # apart when the level itself is not shown.
        logger.warning(f"  ! {warning}")
