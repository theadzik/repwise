"""How a change, a plan and a finding are printed.

Report lines go out through the standard library logger, the way `log.py`
describes: INFO is the report the user asked for and lands on stdout, WARNING
and above are problems and land on stderr. A use case therefore emits its
report without knowing where the report goes - only `main()` decides that.

A plan is read as the workout is performed, top to bottom, so the lines are
built before any of them is logged and laid out in that order rather than in
the order the planner happened to decide things. Each kind of change therefore
renders itself and says nothing about where it goes; `report_plan` places it.
"""

import logging
from dataclasses import dataclass

from ..domain.models import ExerciseSpec
from ..domain.progression import Target
from ..planner import (
    Change,
    GapChange,
    NoteChange,
    Plan,
    RestChange,
    SetChange,
    SkipChange,
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

#: How wide the before and after columns are. Enough for a target written out
#: set by set - `9,9,8,8 x 30 kg` - so that a ramp does not push every reason
#: beside it out of line.
COLUMN = 17

#: The order one exercise's own lines go in, when it has more than one. What
#: the step is comes before what it asks of you, which comes before how it is
#: described - the same shape the whole report used to have, kept per exercise.
SHAPE, TARGET, SETS, REST, SKIP, NOTE = range(6)


@dataclass(frozen=True)
class Line:
    """One rendered line, and where in the workout it belongs.

    `at` is the exercise's place in workouts.yaml and then the rank above, so
    that sorting on it alone puts every line under the exercise it is about.
    """

    at: tuple[int, int]
    text: str


def describe(spec: ExerciseSpec, target: Target) -> str:
    """Render a target the way the exercise is actually measured.

    A ramped target is written out set by set - `9,9,8,8` - because the whole
    point of it is that the sets differ, and a single figure could only ever
    name one of them.
    """
    figure = target.spread(spec.sets, spec.rep_step)
    if spec.time_based:
        return f"{figure} s"
    if spec.bodyweight:
        return f"{figure} reps"
    return f"{figure} x {target.weight:g} kg"


def report_change(change: Change, force_flag: str | None = None) -> str:
    flag = force_flag or ("*" if change.moved else " ")
    return (
        f"{flag} {change.spec.name:<40}"
        f" {describe(change.spec, change.old):>{COLUMN}}"
        f"  ->  {describe(change.spec, change.new):<{COLUMN}} ({change.reason})"
    )


def report_prescribed(name: str, old: str, new: str, source: str) -> str:
    """A number workouts.yaml moved, in the same columns as a target.

    Shown like a target rather than hidden like a note: these change how the
    workout is performed, and are on the watch from the next sync.
    """
    return f"* {name:<40} {old:>{COLUMN}}  ->  {new:<{COLUMN}} ({source})"


def report_note(change: NoteChange) -> str:
    """The one-line note the watch shows, which workouts.yaml also decides.

    Shown rather than hidden because a config edit that only touches the
    programming - a rep range, a weight step - moves no target at all, and the
    run would otherwise say every exercise is up to date while still having a
    reason to write.
    """
    return report_prescribed(
        change.spec.name,
        change.old or "no note",
        change.new,
        "note from workouts.yaml",
    )


def report_rest(change: RestChange) -> str:
    return report_prescribed(
        change.spec.name,
        f"{change.old} s rest",
        f"{change.new} s rest",
        "rest from workouts.yaml",
    )


def report_sets(change: SetChange) -> str:
    return report_prescribed(
        change.spec.name,
        f"{change.old} sets",
        f"{change.new} sets",
        "sets from workouts.yaml",
    )


def report_skips(change: SkipChange) -> str:
    """A group that had been dropping the rest after its final set."""
    return report_prescribed(
        change.spec.name,
        "no last rest",
        "rest after every set",
        "was skipping the last rest",
    )


def report_gaps(change: GapChange) -> str:
    """The rest between exercises: one line for the workout, not one per gap."""
    return report_prescribed(
        "Between exercises",
        change.before,
        f"{change.new} s rest",
        f"{change.gaps} gap(s), from workouts.yaml",
    )


#: The marker each kind of structural change prints under. Deliberately not
#: `*`: these change what the workout is, not what it asks of you.
STRUCTURE = {"added": "+", "removed": "-", "moved": "~"}


def report_shaped(marker: str, name: str, target: str, detail: str) -> str:
    """A structural change, in the same columns as a target.

    The before column is always empty - none of these moves a figure to
    another figure - so what a newly built exercise starts at lands under the
    targets around it and is read the same way, and why the line is there goes
    where every other reason goes. An exercise being dropped or moved has no
    figure at all, and shows an empty column rather than an arrow into one.
    """
    arrow = "  ->  " if target else "      "
    return f"{marker} {name:<40} {'':>{COLUMN}}{arrow}{target:<{COLUMN}} ({detail})"


def report_structure(change: StructureChange) -> str:
    """An exercise the config added, dropped, or put somewhere else.

    An addition that looks like a rename carries the exercise it takes over
    from, so the removal it pairs with needs no line of its own: one line, in
    the new exercise's place, saying both halves of what happened.
    """
    target = ""
    if change.kind == "added" and change.spec and change.target:
        target = f"{change.spec.sets} x {describe(change.spec, change.target)}"
        detail = f"new at position {change.position}"
        if change.replaces:
            detail = f"replaces {change.replaces}, {detail}"
    elif change.kind == "removed":
        detail = "removed: no longer in workouts.yaml"
    else:
        detail = f"moved to position {change.position}"

    return report_shaped(STRUCTURE.get(change.kind, " "), change.name, target, detail)


def _places(plan: Plan) -> dict[str, int]:
    """Where each exercise sits in workouts.yaml, by the name lines print under."""
    return {spec.name: place for place, spec in enumerate(plan.workout.exercises)}


def _lines(plan: Plan, force_flag: str | None) -> list[Line]:
    """Every line the plan has to show, each tagged with where it belongs.

    An exercise the config no longer names has no place among the ones it does,
    so a removal goes after them; the rest between exercises belongs to the
    whole workout and goes last of all.
    """
    places = _places(plan)
    dropped = len(places)

    def place(name: str) -> int:
        return places.get(name, dropped)

    lines = [
        Line((place(change.name), SHAPE), report_structure(change))
        for change in plan.reshaped
    ]
    lines += [
        Line((place(change.spec.name), TARGET), report_change(change, force_flag))
        for change in plan.changes
    ]
    lines += [
        Line((place(count.spec.name), SETS), report_sets(count)) for count in plan.sets
    ]
    lines += [
        Line((place(rest.spec.name), REST), report_rest(rest)) for rest in plan.rests
    ]
    lines += [
        Line((place(skip.spec.name), SKIP), report_skips(skip)) for skip in plan.skips
    ]
    lines += [
        Line((place(note.spec.name), NOTE), report_note(note)) for note in plan.notes
    ]
    if plan.gaps:
        lines.append(Line((dropped + 1, SHAPE), report_gaps(plan.gaps)))
    return lines


def report_plan(plan: Plan, force_flag: str | None = None) -> None:
    # Sorted rather than emitted as they were decided, so that an exercise
    # added halfway down the workout is reported halfway down the report.
    # Stable, so two lines with the same claim on a place keep the order the
    # planner put them in.
    for line in sorted(_lines(plan, force_flag), key=lambda line: line.at):
        logger.info(line.text)

    # Last, and together: these are the only lines that go to stderr, so where
    # they land among the others is not ours to decide anyway.
    for warning in plan.warnings:
        # The marker survives the move to logging: it still sets a warning
        # apart when the level itself is not shown.
        logger.warning(f"  ! {warning}")
