"""How a plan and a finding are printed.

Report lines go out through the standard library logger, the way `log.py`
describes: INFO is the report the user asked for and lands on stdout, WARNING
and above are problems and land on stderr. A use case therefore emits its
report without knowing where the report goes - only `main()` decides that.

A plan prints as a table, one row per exercise, in the order workouts.yaml
puts them. The planner decides changes a kind at a time - targets, sets, rests,
notes, structure - which is not how a plan is read, so everything about one
exercise is gathered back together here and written as one row.
"""

import logging
from dataclasses import dataclass, fields

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

#: The marker a structural change prints under. Deliberately not `*`: these
#: change what the workout is, not what it asks of you.
MARKERS = {"added": "+", "removed": "-", "moved": "~"}
#: And what to call it in the action column, which says the same thing in a
#: word so that the markers need not be memorised.
ACTIONS = {"added": "build", "removed": "drop", "moved": "move"}


@dataclass(frozen=True)
class Row:
    """One line of the table, before its columns are sized.

    Every cell is already written out as text: what is worth saying about an
    exercise is decided here, and how wide the columns have to be to hold it is
    decided once, afterwards, over all the rows together.
    """

    marker: str = ""
    place: str = ""
    name: str = ""
    action: str = ""
    sets: str = ""
    before: str = ""
    after: str = ""
    config: str = ""
    why: str = ""


HEADING = Row(place="#", name="EXERCISE", action="ACTION", sets="SETS",
              before="BEFORE", after="AFTER", config="CONFIG", why="WHY")  # fmt: skip


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

    @property
    def config(self) -> list[str]:
        """What workouts.yaml would rewrite here, named rather than spelled out.

        These are prescribed rather than earned, and the file itself says what
        to; that they are about to be written is the part worth a column.
        """
        named = [
            ("sets", self.sets),
            ("rest", self.rest),
            ("last-rest", self.skip),
            ("note", self.note),
        ]
        return [label for label, change in named if change is not None]


def describe(spec: ExerciseSpec, target: Target) -> str:
    """Render a target the way the exercise is actually measured."""
    figure = target.spread(spec.sets, spec.rep_step)
    if spec.time_based:
        return f"{figure} s"
    if spec.bodyweight:
        return f"{figure} reps"
    return f"{figure} x {target.weight:g} kg"


def moved_to(change: Change) -> str:
    """Which way a target went, which is not always up: a second miss eases it.

    Compared on the load first and the reps after, because that is the order
    the rules spend them in.
    """
    if change.old == change.new:
        return "hold"
    was = (change.old.weight, change.old.reps, change.old.lead)
    now = (change.new.weight, change.new.reps, change.new.lead)
    return "advance" if now > was else "ease"


def shape_why(shape: StructureChange) -> str:
    """Why an exercise arrived, left or moved.

    Where it now sits has a column of its own, so a move says where it came
    from instead, which is the part the table does not already show.
    """
    if shape.kind == "removed":
        return "no longer in workouts.yaml"
    if shape.kind == "moved":
        return f"from position {shape.previous}"
    if shape.replaces:
        return f"replaces {shape.replaces}"
    return "new in workouts.yaml"


def exercise_row(
    place: int | None, name: str, each: Gathered, spec: ExerciseSpec | None
) -> Row:
    """One exercise's row: what it is, what it asks for, and why.

    The before and after columns are the target's, since that is the number a
    plan is read for. An exercise being built shows what it starts at and has
    no before; one being dropped or shaped from the config alone has neither,
    and says what is happening in the columns that are left.

    `spec` is what workouts.yaml says about it, and is None for the one row
    that has no entry there: a step being dropped.
    """
    why = []
    before = after = ""

    if each.shape:
        why.append(shape_why(each.shape))
        if each.shape.spec and each.shape.target:
            after = describe(each.shape.spec, each.shape.target)

    if each.change:
        before = describe(each.change.spec, each.change.old)
        after = describe(each.change.spec, each.change.new)
        why.append(each.change.reason)

    config = each.config
    if config and not why:
        why.append("from workouts.yaml")

    if each.sets:
        sets = f"{each.sets.old} -> {each.sets.new}"
    else:
        sets = str(spec.sets) if spec else ""

    return Row(
        marker=row_marker(each, config),
        place="" if place is None else str(place),
        name=name,
        action=row_action(each),
        sets=sets,
        before=before,
        after=after,
        config=" ".join(config),
        why="; ".join(why),
    )


def row_marker(each: Gathered, config: list[str]) -> str:
    """Whether this run writes the exercise, and what kind of change it is.

    A blank marker is the one row you can skip: read, judged, left alone.
    """
    if each.shape:
        return MARKERS.get(each.shape.kind, " ")
    if config or (each.change and each.change.moved):
        return "*"
    return " "


def row_action(each: Gathered) -> str:
    """What is happening, in a word.

    An exercise arriving, leaving or moving is the larger fact about it and
    takes the column; otherwise it is what the session did to the target, and
    an exercise no session touched holds it by definition.
    """
    if each.shape:
        return ACTIONS.get(each.shape.kind, "")
    return moved_to(each.change) if each.change else "hold"


def gaps_row(change: GapChange) -> Row:
    """The rest between exercises: one row for the workout, not one per gap."""
    return Row(
        marker="*",
        name="Between exercises",
        action="retime",
        before=change.before,
        after=f"{change.new} s rest",
        why=f"{change.gaps} gap(s), from workouts.yaml",
    )


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


def rows(plan: Plan) -> list[Row]:
    """The plan as rows, in the order workouts.yaml puts the exercises in.

    An exercise the config no longer names has no place among the ones it
    does, so a removal follows them, and the rest between exercises - which
    belongs to the whole workout rather than to any one of them - comes last.

    The workout's own name is not among them. Every row here is something the
    workout contains; the name is what the workout *is*, so it is announced
    with the heading instead - see `report_plan`.
    """
    described = {spec.name: spec for spec in plan.workout.exercises}
    places = {name: place for place, name in enumerate(described, 1)}
    gathered = gather(plan)

    ordered = sorted(gathered, key=lambda name: places.get(name, len(places) + 1))
    built = [
        exercise_row(places.get(name), name, gathered[name], described.get(name))
        for name in ordered
    ]
    if plan.gaps:
        built.append(gaps_row(plan.gaps))
    return built


def render(built: list[Row]) -> list[str]:
    """Pad the rows into columns, each as wide as its widest cell.

    Sized to the rows in hand rather than to fixed widths, so a workout of
    short names is not read across a gap of spaces and a long one still lines
    up. The before column meets the arrow, so it is the one right-aligned.
    """
    width = {
        field.name: max(len(getattr(row, field.name)) for row in [HEADING, *built])
        for field in fields(Row)
    }

    def line(row: Row, arrow: str) -> str:
        return (
            f"{row.marker:<1} {row.place:>{width['place']}} "
            f"{row.name:<{width['name']}} {row.action:<{width['action']}} "
            f"{row.sets:<{width['sets']}} {row.before:>{width['before']}}{arrow}"
            f"{row.after:<{width['after']}} {row.config:<{width['config']}} {row.why}"
        ).rstrip()

    return [line(HEADING, "      ")] + [line(row, joins(row)) for row in built]


def joins(row: Row) -> str:
    """What sits between the two value columns.

    `==` where the two agree, because a target being asked for again is a
    result in its own right - the session was read, judged, and the number
    stands - and an arrow to the same figure reads like a change that isn't.
    """
    if not row.after:
        return "      "
    return "  ==  " if row.before == row.after else "  ->  "


def report_plan(plan: Plan) -> None:
    # Above the table and under the heading the caller has just printed, which
    # is where the workout is being named anyway. It is a fact about the
    # workout rather than about anything in it, and a table of exercises is no
    # place to say it.
    if plan.name:
        logger.info(f"Renaming: {plan.name.was} -> {plan.name.new}")

    for text in render(rows(plan)):
        logger.info(text)

    # Last, and together: these are the only lines that go to stderr, so where
    # they land among the others is not ours to decide anyway.
    for warning in plan.warnings:
        # The marker survives the move to logging: it still sets a warning
        # apart when the level itself is not shown.
        logger.warning(f"! {warning}")
