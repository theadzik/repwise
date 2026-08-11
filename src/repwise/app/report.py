"""How a change, a plan and a finding are printed.

Report lines go out through the standard library logger, the way `log.py`
describes: INFO is the report the user asked for and lands on stdout, WARNING
and above are problems and land on stderr. A use case therefore emits its
report without knowing where the report goes - only `main()` decides that.

One exercise gets one line, wherever workouts.yaml puts it. Everything a plan
decided about it - where it now sits, what it asks for, how many sets, resting
how long, described how - is gathered first and written once, so the report is
read down the page as the workout is performed rather than as the planner
happened to decide things.
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
#: How wide the name is. The longest exercise name anyone writes, near enough,
#: and what keeps the columns beside it in line when one is longer.
NAME = 40

#: The marker each kind of structural change prints under. Deliberately not
#: `*`: these change what the workout is, not what it asks of you.
STRUCTURE = {"added": "+", "removed": "-", "moved": "~"}


@dataclass
class Gathered:
    """Everything one plan decided about one exercise.

    Filled from the lists a `Plan` carries, which are each about one kind of
    change; this is the same information the other way round, by exercise,
    which is how it is read.
    """

    shape: StructureChange | None = None
    change: Change | None = None
    sets: SetChange | None = None
    rest: RestChange | None = None
    skip: SkipChange | None = None
    note: NoteChange | None = None


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


def report_line(marker: str, name: str, old: str, new: str, detail: str) -> str:
    """The one shape every line of a plan has.

    An exercise being dropped or moved has no figure to show, so it gets an
    empty column rather than an arrow into one; a newly built exercise has an
    after and no before, which is exactly what it is.
    """
    arrow = "  ->  " if new else "      "
    return f"{marker} {name:<{NAME}} {old:>{COLUMN}}{arrow}{new:<{COLUMN}} ({detail})"


def report_gaps(change: GapChange) -> str:
    """The rest between exercises: one line for the workout, not one per gap."""
    return report_line(
        "*",
        "Between exercises",
        change.before,
        f"{change.new} s rest",
        f"{change.gaps} gap(s), from workouts.yaml",
    )


def shape_detail(shape: StructureChange) -> str:
    """Why a structural line is there, in the words the marker needs explaining.

    An addition that looks like a rename names what it takes over from, so the
    removal it pairs with needs no line of its own: one line, in the new
    exercise's place, saying both halves of what happened.
    """
    if shape.kind == "removed":
        return "removed: no longer in workouts.yaml"
    if shape.kind != "added":
        return f"moved to position {shape.position}"
    if shape.replaces:
        return f"replaces {shape.replaces}, new at position {shape.position}"
    return f"new at position {shape.position}"


def prescribed(each: Gathered) -> list[tuple[str, str, str]]:
    """What workouts.yaml moved on this exercise: what it is called, and its
    before and after.

    In the order the columns are offered to them, so an exercise with no target
    to show shows the first of these instead. The skip is here for its figures
    only - it is not something the config asks for, but something it stops.
    """
    entries = []
    if each.sets:
        entries.append(("sets", f"{each.sets.old} sets", f"{each.sets.new} sets"))
    if each.rest:
        entries.append(("rest", f"{each.rest.old} s rest", f"{each.rest.new} s rest"))
    if each.skip:
        entries.append(("last rest", "no last rest", "rest after every set"))
    if each.note:
        entries.append(("note", each.note.old or "no note", each.note.new))
    return entries


def report_marker(
    each: Gathered, config: list[tuple[str, str, str]], force: str | None
) -> str:
    """What the line is flagged with: what it is, before what it asks for.

    A structural marker wins, because an exercise arriving, leaving or moving
    is the larger fact about it; `*` then means something would be written, and
    a blank that the exercise was read and left alone.
    """
    if each.shape:
        return STRUCTURE.get(each.shape.kind, " ")
    if each.change and force:
        return force
    if config or (each.change and each.change.moved):
        return "*"
    return " "


def report_exercise(name: str, each: Gathered, force_flag: str | None = None) -> str:
    """One exercise's whole line: the change worth showing, and the rest named.

    The columns go to what was earned in a session, or failing that to what the
    config moved, because a target is the number anyone reads a plan for. The
    others are named beside it rather than spelled out: that they will be
    written is the useful part, and the file itself says what to.
    """
    config = prescribed(each)
    reasons = []
    old = new = ""

    if each.shape:
        reasons.append(shape_detail(each.shape))
        if each.shape.kind == "added" and each.shape.spec and each.shape.target:
            spec, target = each.shape.spec, each.shape.target
            new = f"{spec.sets} x {describe(spec, target)}"

    if each.change:
        old = describe(each.change.spec, each.change.old)
        new = describe(each.change.spec, each.change.new)
        reasons.append(each.change.reason)
    elif not new and config:
        _, old, new = config[0]

    if each.skip:
        reasons.append("was skipping the last rest")

    detail = ", ".join(reasons)
    named = [label for label, _, _ in config if label != "last rest"]
    if named:
        # Joined on with a semicolon, because the list itself is separated by
        # commas: `hit 8 on every set; sets, note from workouts.yaml` is two
        # things, and `hit 8 on every set, sets, note ...` looks like four.
        asked = f"{', '.join(named)} from workouts.yaml"
        detail = f"{detail}; {asked}" if detail else asked

    return report_line(report_marker(each, config, force_flag), name, old, new, detail)


def gather(plan: Plan) -> dict[str, Gathered]:
    """Everything the plan says, by the exercise it says it about."""
    found: dict[str, Gathered] = {}

    def of(name: str) -> Gathered:
        return found.setdefault(name, Gathered())

    for shape in plan.reshaped:
        of(shape.name).shape = shape
    for change in plan.changes:
        of(change.spec.name).change = change
    for count in plan.sets:
        of(count.spec.name).sets = count
    for rest in plan.rests:
        of(rest.spec.name).rest = rest
    for skip in plan.skips:
        of(skip.spec.name).skip = skip
    for note in plan.notes:
        of(note.spec.name).note = note
    return found


def report_plan(plan: Plan, force_flag: str | None = None) -> None:
    """The whole plan, an exercise to a line, in the config's own order.

    An exercise the config no longer names has no place among the ones it does,
    so a removal goes after them; the rest between exercises belongs to the
    whole workout and goes last of all.
    """
    places = {spec.name: place for place, spec in enumerate(plan.workout.exercises)}
    gathered = gather(plan)

    for name in sorted(gathered, key=lambda name: places.get(name, len(places))):
        logger.info(report_exercise(name, gathered[name], force_flag))

    if plan.gaps:
        logger.info(report_gaps(plan.gaps))

    # Last, and together: these are the only lines that go to stderr, so where
    # they land among the others is not ours to decide anyway.
    for warning in plan.warnings:
        # The marker survives the move to logging: it still sets a warning
        # apart when the level itself is not shown.
        logger.warning(f"  ! {warning}")
