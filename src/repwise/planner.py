"""Decide what should change, without changing anything yet.

The planner matches Garmin's workout steps to the exercises declared in
workouts.yaml, runs the progression rules over them, and reports the result.
It mutates the workout payload it is handed but performs no I/O, so a caller
can inspect a plan and discard it -- which is what a dry run does.
"""

from collections.abc import Container
from dataclasses import dataclass, field, replace
from typing import Any

from .domain.matching import ExerciseIndex, normalise
from .domain.models import Config, ExerciseSpec, Workout
from .domain.progression import (
    PerformedSet,
    Session,
    Target,
    miss_streak,
    next_target,
    working_weight,
)
from .errors import ActivityNotFound
from .garmin.payloads import (
    GENERATED_NOTE,
    ExerciseBlock,
    apply_block,
    apply_last_rest,
    apply_note,
    apply_rest,
    apply_sets,
    block_target,
    executed_exercises,
    is_rest,
    is_timed_rest,
    iter_exercise_blocks,
    new_group,
    new_rest,
    set_exercise_steps,
    skips_last_rest,
    step_category,
    step_exercise_name,
    step_note,
    step_rest,
    steps_between,
)

Performed = tuple[dict[str, list[PerformedSet]], dict[str, list[PerformedSet]]]
#: What each exercise did in the sessions before this one, newest first, keyed
#: by normalised `garmin_name`. Only ever read to see how long an exercise had
#: been stalling, so an exercise missing from it simply had no stall.
History = dict[str, list[Session]]


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
class NoteChange:
    """One exercise's step note, before and after.

    Prescribed by workouts.yaml exactly as a rest is, and reported the same
    way: a note only ever moves because the config moved, and a run whose
    targets all held still is precisely when it needs saying out loud.
    """

    spec: ExerciseSpec
    #: What the step said before. Empty where the step had no note at all.
    old: str
    new: str


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
class SkipChange:
    """One exercise that was dropping the rest after its final set.

    Only ever recorded in one direction: every set gets its rest, so there is
    no old and new value to hold, just which exercise had been the exception.
    """

    spec: ExerciseSpec


@dataclass(frozen=True)
class GapChange:
    """The rest between exercises, before and after.

    One record for the whole workout rather than one per join: Garmin holds a
    separate step between each pair of exercises, but the config carries a
    single number for all of them, and reporting each separately would say the
    same thing eight times.
    """

    #: How many of those steps this changes.
    gaps: int
    #: What each said before: seconds, or None where it waited for the button.
    was: tuple[int | None, ...]
    new: int

    @property
    def before(self) -> str:
        """The old value, when they all agreed on one."""
        distinct = set(self.was)
        if len(distinct) > 1:
            return "mixed"
        return "lap button" if self.was[0] is None else f"{self.was[0]} s rest"


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
    #: Garmin's category for it, kept so that an addition and a removal can be
    #: recognised as the same movement under two names.
    category: str | None = None
    #: The exercise being dropped that this one looks like a renaming of. Only
    #: ever set on an addition, and only where `_same_movement` paired the two,
    #: so that the report can say in the added exercise's own place what would
    #: otherwise be a line here, a line further down, and a paragraph below.
    replaces: str | None = None
    #: Where it used to sit, on a move. The report gives where an exercise is
    #: now a column of its own, so where it came from is the new thing to say
    #: about one that moved.
    previous: int | None = None

    @property
    def garmin_name(self) -> str:
        """What Garmin calls it, whichever side of the change it came from."""
        return self.spec.garmin_name if self.spec else self.name


@dataclass(frozen=True)
class Plan:
    """What a single workout would become."""

    workout: Workout
    payload: dict[str, Any]
    changes: list[Change]
    warnings: list[str]
    #: Exercises whose notes field was rewritten, which is its own reason to
    #: save a workout: editing workouts.yaml moves no target on its own.
    notes: list[NoteChange] = field(default_factory=list)
    #: Exercises whose rest step was rewritten. Config-driven like the notes,
    #: and a reason to save for the same reason.
    rests: list[RestChange] = field(default_factory=list)
    #: Exercises whose repeat group now prescribes a different number of sets.
    sets: list[SetChange] = field(default_factory=list)
    #: Exercises whose repeat group was skipping the rest after its final set.
    skips: list[SkipChange] = field(default_factory=list)
    #: Exercises added, removed or moved. Config-driven again, and the only
    #: kind of change that alters what the workout is rather than what it asks.
    structure: list[StructureChange] = field(default_factory=list)
    #: The rest between exercises, when the config moved it. One per workout,
    #: because that is how the config expresses it.
    gaps: GapChange | None = None

    @property
    def moved(self) -> list[Change]:
        return [change for change in self.changes if change.moved]

    @property
    def reshaped(self) -> list[StructureChange]:
        """The structural changes that are worth a line of their own.

        Which is all of them except a removal some addition already says it
        replaces: that has been reported in the added exercise's own line, and
        counting it separately would promise a line nothing prints.
        """
        replaced = {change.replaces for change in self.structure if change.replaces}
        return [
            change
            for change in self.structure
            if not (change.kind == "removed" and change.name in replaced)
        ]

    @property
    def writable(self) -> bool:
        """Whether this plan has anything worth sending to Garmin."""
        return bool(
            self.moved
            or self.notes
            or self.rests
            or self.sets
            or self.skips
            or self.structure
            or self.gaps
        )


def find_workout(config: Config, activity_name: str) -> Workout:
    """Which workout does an activity belong to, by name prefix."""
    for workout in config:
        if workout.claims(activity_name):
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


def logged_for(spec: ExerciseSpec, performed: Performed) -> list[PerformedSet]:
    """Sets logged for an exercise, by its configured name then its category.

    What is left of the lookup when there is no workout step to consult -
    reading back a past session, where all we have is the config and what the
    watch recorded.
    """
    by_name, by_category = performed
    found = by_name.get(normalise(spec.garmin_name))
    if found:
        return found
    if spec.garmin_category:
        return by_category.get(normalise(spec.garmin_category)) or []
    return []


def _logged_for(
    spec: ExerciseSpec, step: dict[str, Any], performed: Performed
) -> list[PerformedSet]:
    """Sets logged for an exercise, tolerating the name Garmin chose."""
    by_name, _ = performed
    # What the step itself is called comes first: Garmin auto-detects the
    # movement while you lift, and the workout is the better guess at which
    # exercise a set belongs to than the config's own name for it.
    logged = by_name.get(normalise(step_exercise_name(step) or ""))
    return logged if logged else logged_for(spec, performed)


def _moved_on(
    spec: ExerciseSpec, current: Target, asked: dict[str, Target] | None
) -> bool:
    """Whether the stored target is no longer the one this session was given.

    Everything the rules decide is relative to what the session was actually
    asked for, and until now that was taken to be whatever the workout holds
    now. It stops being true the moment anything moves the target - above all
    this tool's own `--apply`, after which the same activity is still the
    latest one and would be judged a second time against the target it just
    earned. Every set would read as short of a figure nobody was aiming at, and
    a second miss on the record is what deloading acts on: running twice would
    walk targets backwards.

    Weights are not compared, only the reps: Garmin's record of an executed
    workout carries no load (see docs/garmin-api.md). A weight change always
    resets the reps with it, so it is caught anyway.

    A hand edit in Connect reads the same way, and gets the same answer for the
    same reason: the session predates the target, so it is not evidence about
    it, and the figure typed in stands.
    """
    if not asked:
        return False
    was = asked.get(normalise(spec.garmin_name))
    if was is None:
        return False  # nothing recorded for it, so no reason to doubt the step
    return (was.reps, was.lead) != (current.reps, current.lead)


def _relay(
    layout: list[list[dict[str, Any]]], position: int, now: list[dict[str, Any]]
) -> bool:
    """Record where an exercise sits now, and say whether the workout moved.

    Only a change in how many steps an exercise occupies - a ramp opening or
    closing - means the workout has to be laid out again. Writing a new figure
    onto the steps already there changes nothing about their order, and
    relaying the workout for that would insert the rests between exercises into
    one that never had any.
    """
    moved = len(now) != len(layout[position])
    layout[position] = now
    return moved


def _streak(
    spec: ExerciseSpec, logged: list[PerformedSet], history: History | None
) -> int:
    """How many sessions in a row this exercise missed before the latest one.

    None at all is the smooth case rather than a missing answer: an exercise
    with no history behind it - a workout trained for the first time, a run
    that could not look further back - has not been stalling as far as anything
    here can tell, and gets the full advance it always did.
    """
    if not history:
        return 0
    past = history.get(normalise(spec.garmin_name))
    if not past:
        return 0
    return miss_streak(spec, past, working_weight(logged))


def _refresh_note(
    block: ExerciseBlock,
    spec: ExerciseSpec,
    notes: list[NoteChange],
    warnings: list[str],
) -> None:
    """Keep the step's notes field showing how the exercise is programmed.

    Only a blank note, or one this tool wrote before, is replaced. Anything
    else is a cue the user typed into Garmin Connect, and overwriting it would
    destroy it silently, so it is reported and left alone instead.

    A ramped exercise has the same note on both of its halves - they are one
    exercise, programmed one way - so a cue typed onto either is enough to
    leave both alone.
    """
    wanted = spec.note
    own = [
        note
        for note in (step_note(step) for step in block.steps)
        if note and not GENERATED_NOTE.match(note)
    ]
    if own:
        warnings.append(
            f"{spec.name}: has its own note, left alone (wanted {wanted!r})"
        )
        return

    stale = [step for step in block.steps if step_note(step) != wanted]
    if not stale:
        return

    # Both halves of a ramped exercise carry the same note, so the first stale
    # one speaks for all of them.
    current = step_note(stale[0])
    for step in stale:
        apply_note(step, wanted)
    notes.append(NoteChange(spec, current, wanted))


def _refresh_rest(
    block: ExerciseBlock,
    spec: ExerciseSpec,
    rests: list[RestChange],
    skips: list[SkipChange],
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

    Whether the last set gets its rest at all is settled first, and for every
    exercise: that is a property of the group rather than of the step, and an
    exercise declaring no `rest` still means the one Garmin holds to be the
    rest after each of its sets.
    """
    _refresh_skips(block, spec, skips)

    if not spec.rest:
        return

    if not block.rest_steps:
        warnings.append(
            f"{spec.name}: rest is not a fixed time in Garmin, left alone "
            f"(wanted {spec.rest}s)"
        )
        return

    # Both halves of a ramped exercise rest for the same interval: it is one
    # exercise, and the config gives it one number.
    stale = [step for step in block.rest_steps if step_rest(step) != spec.rest]
    if not stale:
        return

    current = step_rest(stale[0])
    for step in stale:
        apply_rest(step, spec.rest)
    rests.append(RestChange(spec, current, spec.rest))


def _refresh_skips(
    block: ExerciseBlock, spec: ExerciseSpec, skips: list[SkipChange]
) -> None:
    """Keep the rest after the final set, which Connect can be told to drop.

    `skipLastRestStep` is a switch on the repeat group, and the one place a set
    can end without the rest the config prescribes for it. An exercise's `rest`
    means every set, so a group set to skip is put back rather than reported:
    this tool builds groups that do not skip, and leaving one that does would
    make the same exercise behave differently in two workouts.

    A ramped exercise is cleared on both of its groups but recorded once, since
    above this module it is one exercise with one rest.
    """
    skipping = [group for group in block.groups if skips_last_rest(group)]
    if not skipping:
        return

    for group in skipping:
        apply_last_rest(group)
    skips.append(SkipChange(spec))


@dataclass
class _Shaping:
    """What the config changed while a workout was being shaped.

    The four lists a `Plan` carries for it, kept together because they are
    filled together: every exercise passes through the same refreshes, and a
    caller that wanted one of them wants all four.
    """

    notes: list[NoteChange] = field(default_factory=list)
    rests: list[RestChange] = field(default_factory=list)
    sets: list[SetChange] = field(default_factory=list)
    skips: list[SkipChange] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _refresh_block(
    block: ExerciseBlock,
    spec: ExerciseSpec,
    current: Target | None,
    shaped: _Shaping,
) -> list[dict[str, Any]] | None:
    """Write everything workouts.yaml decides about one exercise.

    The note, the rest and the set count describe the programming rather than
    the progress, so they are applied whether or not a session moved anything -
    which is why both planners start an exercise here.

    Returns what `_refresh_sets` returned: the steps the exercise now occupies
    when its count was rewritten, and None when it was not.
    """
    _refresh_note(block, spec, shaped.notes, shaped.warnings)
    _refresh_rest(block, spec, shaped.rests, shaped.skips, shaped.warnings)
    return _refresh_sets(block, spec, current, shaped.sets, shaped.warnings)


def _refresh_sets(
    block: ExerciseBlock,
    spec: ExerciseSpec,
    current: Target | None,
    sets: list[SetChange],
    warnings: list[str],
) -> list[dict[str, Any]] | None:
    """Keep the repeat group prescribing as many sets as the config asks for.

    Garmin counts sets as the iterations of the group around an exercise, so an
    exercise it performs once may have no group at all. Building one around a
    step Garmin already holds is a change of shape rather than of number, and
    is left to Connect: the step is reported and kept as it is.

    Returns the steps the exercise now occupies when the count was rewritten,
    since a ramped exercise splits its sets across two groups and changing the
    total has to redistribute them - which can leave it needing one group where
    it had two. None when nothing was rewritten.
    """
    if block.sets == spec.sets:
        return None

    if block.group is None:
        warnings.append(
            f"{spec.name}: {spec.sets} sets in config, but Garmin performs it "
            f"once, with no repeat group to count them"
        )
        return None

    was = block.sets
    if current is None:
        # Nothing readable to redistribute, so put the whole count on the group
        # that speaks for the exercise and leave the rest as Garmin has it.
        apply_sets(block.group, spec.sets)
        sets.append(SetChange(spec, was, spec.sets))
        return None

    outers = apply_block(block, spec, current)
    sets.append(SetChange(spec, was, spec.sets))
    return outers


def _refresh_gaps(workout: Workout, payload: dict[str, Any]) -> GapChange | None:
    """Keep the rest between exercises at the one the workout asks for.

    Garmin's own default there is a wait for the lap button, which is exactly
    what `rest_between_exercises` exists to change, so declaring it is taken as
    the instruction to make those steps count down. That is the opposite of the
    stance on an exercise's own `rest`, deliberately: this key was added for
    the conversion, while that one has always meant "how long the interval is".

    Leaving the key out is having no opinion, and whatever Garmin holds -
    button presses, or intervals set by hand in Connect - is left alone.
    """
    wanted = workout.rest_between
    if wanted is None:
        return None

    blocks = list(iter_exercise_blocks(payload))
    gaps = [step for step in steps_between(payload, blocks) if is_rest(step)]
    stale = [
        step for step in gaps if not is_timed_rest(step) or step_rest(step) != wanted
    ]
    if not stale:
        return None

    was = tuple(step_rest(step) if is_timed_rest(step) else None for step in stale)
    for step in stale:
        apply_rest(step, wanted)
    return GapChange(len(stale), was, wanted)


def _same_movement(added: StructureChange, removed: StructureChange) -> bool:
    """Whether these two look like one exercise under two names.

    Sharing Garmin's category is the strong signal, since that is what survives
    a rename. Failing that, one name containing the other catches the usual
    slip - WEIGHTED_LEG_CURL against LEG_CURL, say - which is exactly the shape
    a hand-typed `garmin_name` goes wrong in.
    """
    both = added.category and removed.category
    if both and normalise(added.category or "") == normalise(removed.category or ""):
        return True

    new, old = normalise(added.garmin_name), normalise(removed.garmin_name)
    return bool(new and old and (new in old or old in new))


def _pair_renames(structure: list[StructureChange]) -> list[StructureChange]:
    """Note on each addition the removal it is probably the same exercise as.

    Removing one and building another is what a mistyped `garmin_name` looks
    like from in here, and it is not a cheap mistake: the target lives in the
    step being dropped, and nothing else remembers it.

    Paired one to one, so that a workout swapping two exercises at once does
    not read as either of them replacing both. The pairing is carried on the
    change rather than turned into prose here, because where a warning belongs
    on the page is the report's business - see `report_plan`.
    """
    unclaimed = [change for change in structure if change.kind == "removed"]

    paired = list(structure)
    for position, change in enumerate(paired):
        if change.kind != "added":
            continue
        removed = next(
            (each for each in unclaimed if _same_movement(change, each)), None
        )
        if removed is None:
            continue
        unclaimed.remove(removed)
        paired[position] = replace(change, replaces=removed.name)
    return paired


def _renames(structure: list[StructureChange]) -> list[str]:
    """Say out loud that a paired addition may be losing a target.

    Reported rather than prevented, because a rename is also what deliberately
    swapping one movement for a variant of it looks like, and this cannot tell
    the two apart. The dry run is where the difference gets noticed, so the
    line only has to be short enough to be read there.
    """
    return [
        f"{change.name} replaces {change.replaces}: if that is a renamed "
        f"garmin_name rather than a swap, its target is lost"
        for change in structure
        if change.replaces
    ]


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


def _reconcile(
    workout: Workout,
    payload: dict[str, Any],
    structure: list[StructureChange],
    added: set[int],
    trusted: Container[str] | None = None,
) -> None:
    """Make the workout hold the exercises workouts.yaml names, in that order.

    Steps Garmin already has are moved rather than rebuilt: the target lives in
    the step and nowhere else, so a rebuilt one would silently restart the
    progression. An exercise the config names but Garmin lacks is built at the
    bottom of its range; one Garmin has but the config no longer names is
    dropped, which is the config being the source of truth taken seriously.

    `trusted` is every name Garmin publishes, and decides how hard a step is
    looked for. A `garmin_name` the catalog knows is matched by name alone: a
    step for some other exercise sharing its category is a different movement,
    and reusing it would leave the old name on the watch while the sets, reps
    and note all changed underneath - the swap looking done everywhere except
    the one place you read it. Rebuilt instead, which restarts the progression,
    because a target earned on one exercise was never the other's to keep.

    Nothing is decided about order beyond what the file says: `set_exercise_steps`
    renumbers, and Garmin sorts by those numbers.
    """
    blocks = list(iter_exercise_blocks(payload))
    index = _index_blocks(blocks)
    gaps = steps_between(payload, blocks)

    # Steps are compared by identity throughout: two exercises can hold equal
    # dictionaries, and it matters which one of them is being moved.
    was = {id(block.outer): position for position, block in enumerate(blocks)}
    labels = {
        id(block.outer): step_exercise_name(block.step)
        or step_category(block.step)
        or "?"
        for block in blocks
    }
    categories = {id(block.outer): step_category(block.step) for block in blocks}

    # A list of steps per exercise, not a step per exercise: a ramped one is
    # held as two groups that have to travel together.
    outers: list[list[dict[str, Any]]] = []
    kept: list[int] = []

    for spec in workout.exercises:
        block = index.find(spec.garmin_name, spec.garmin_category, trusted=trusted)
        if block is None or id(block.outer) in kept:
            # Nothing in Garmin answers to this, or an earlier exercise already
            # claimed the step that does. Either way it needs one of its own.
            target = Target(spec.rep_low, spec.start_weight)
            group = new_group(spec, target)
            outers.append([group])
            added.add(id(group))
            structure.append(
                StructureChange(
                    "added",
                    spec.name,
                    len(outers),
                    spec,
                    target,
                    spec.garmin_category,
                )
            )
            continue

        outers.append(block.outers)
        kept.append(id(block.outer))
        # Now that a spec claims it, call it what the config calls it. Only an
        # exercise being removed keeps the name Garmin knows it by, there being
        # nothing else left to call it.
        labels[id(block.outer)] = spec.name

    for block in blocks:
        if id(block.outer) not in kept:
            structure.append(
                StructureChange(
                    "removed",
                    labels[id(block.outer)],
                    category=categories[id(block.outer)],
                )
            )

    at = {id(steps[0]): position for position, steps in enumerate(outers)}
    for ident in _out_of_order(kept, was, at):
        structure.append(
            StructureChange(
                "moved", labels[ident], at[ident] + 1, previous=was[ident] + 1
            )
        )

    if structure:
        set_exercise_steps(
            payload, outers, _gaps_for(outers, gaps, workout.rest_between)
        )


def _out_of_order(
    kept: list[int], was: dict[int, int], now: dict[int, int]
) -> list[int]:
    """The fewest exercises whose moving accounts for the new order.

    Everything that held its relative place is left out of the report, and what
    is left is the complement of the longest such run. Without this, moving the
    plank from last to first reads as every other exercise moving down one -
    true of their positions, and useless to read.

    More than one run can be the longest: swap two exercises around a third and
    any two of them explain it, so which two are named is a real choice. It
    goes to the run holding the exercises that are still at the position they
    were at, which is the one that names the exercises you actually moved -
    otherwise swapping the second and fourth can report the third, which never
    moved at all, and say nothing about the fourth, which crossed it.

    Longest increasing subsequence, quadratic, over a handful of exercises.
    """
    if not kept:
        return []

    before = [was[ident] for ident in kept]
    #: Whether each is at the same position as before, which only breaks ties.
    still = [int(was[ident] == now[ident]) for ident in kept]
    #: What a run is judged on: how many exercises it holds, and then how many
    #: of those never moved. Longer always wins; the second only settles a tie.
    best = [(1, stayed) for stayed in still]
    came_from = [-1] * len(before)
    for later in range(len(before)):
        for earlier in range(later):
            if before[earlier] >= before[later]:
                continue
            length, stayed = best[earlier]
            through = (length + 1, stayed + still[later])
            if through > best[later]:
                best[later] = through
                came_from[later] = earlier

    end = max(range(len(before)), key=lambda position: best[position])
    in_place = set()
    while end != -1:
        in_place.add(end)
        end = came_from[end]

    return [ident for position, ident in enumerate(kept) if position not in in_place]


def _gaps_for(
    outers: list[list[dict[str, Any]]],
    existing: list[dict[str, Any]],
    seconds: int | None,
) -> list[dict[str, Any]]:
    """One step per join, reusing what was there and building the shortfall.

    A gap built here starts at whatever the workout asks for, so that a new
    join - or a whole new workout - needs no correcting afterwards. With no
    `rest_between_exercises` it is a wait for the lap button, which is Garmin's
    own default and so the least surprising thing to invent.
    """
    needed = max(len(outers) - 1, 0)
    gaps = existing[:needed]
    return gaps + [new_rest(seconds) for _ in range(needed - len(gaps))]


def plan_workout(  # noqa: PLR0913 - each argument is one independent input
    workout: Workout,
    payload: dict[str, Any],
    performed: Performed | None = None,
    history: History | None = None,
    asked: dict[str, Target] | None = None,
    *,
    trusted: Container[str] | None = None,
) -> Plan:
    """Bring a workout in line with the config, and advance what was trained.

    Two kinds of change, in that order. The config decides the shape - which
    exercises, in what order, resting how long, described how - and is applied
    whether or not anything was trained. The session decides the targets, and
    only for the exercises it actually contains: pass no `performed` at all for
    a workout with no session behind it, and only the first kind happens.

    `history` is the sessions before this one, newest first, from which how
    badly each exercise had been stalling is read. Without it every exercise is
    treated as having hit last time, which is the smooth case and the behaviour
    this tool had before granular progression existed.

    `asked` is what this session itself was performed against. Without it the
    stored target is assumed to be that, which is only true until something
    moves it - this run's own `--apply`, most of all. See `_moved_on`.

    `trusted` is Garmin's published exercise names, which decide whether an
    exercise Garmin holds under another name is reused or rebuilt. Without it
    every name falls back to its category, which is the behaviour this tool had
    before it could tell a real exercise name from an invented one.
    """
    specs = index_specs(workout.exercises)

    changes: list[Change] = []
    shaped = _Shaping()
    structure: list[StructureChange] = []
    added: set[int] = set()

    _reconcile(workout, payload, structure, added, trusted)
    structure = _pair_renames(structure)
    shaped.warnings.extend(_renames(structure))
    gaps = _refresh_gaps(workout, payload)

    # Where each exercise sits, so that one which gains or loses a group as its
    # target ramps can be laid back down in the right place afterwards.
    blocks = list(iter_exercise_blocks(payload))
    layout = [block.outers for block in blocks]
    spare = steps_between(payload, blocks)
    reshaped = False

    for position, block in enumerate(blocks):
        step = block.step
        label = step_exercise_name(step) or step_category(step)

        spec = _match(step, specs)
        if spec is None:
            # Unreachable after reconciling, which keeps only what the config
            # names, but a step that matched nothing is not one to write to.
            shaped.warnings.append(f"{label}: not in workouts.yaml, skipped")
            continue

        current = block_target(block, spec)

        # Before the target checks below: what the config says about this
        # exercise holds whether or not this session moved anything.
        recounted = _refresh_block(block, spec, current, shaped)
        if recounted is not None:
            reshaped |= _relay(layout, position, recounted)

        if performed is None or id(block.outer) in added:
            # Either no session to learn from, or a step this run has just
            # built, which already holds exactly what the config asks for.
            continue

        if current is not None and _moved_on(spec, current, asked):
            # Already learned from, or overtaken by a hand edit. Either way this
            # session has nothing left to say about a target it never saw.
            changes.append(Change(spec, current, current, "up to date"))
            continue

        if current is None:
            kind = "time" if spec.time_based else "rep"
            shaped.warnings.append(f"{label}: step has no {kind} target, skipped")
            continue

        logged = _logged_for(spec, step, performed)
        if not logged:
            shaped.warnings.append(f"{spec.name}: not found in the activity, skipped")
            continue

        if spec.time_based:
            # Garmin logs a hold as 1 rep; the duration is the real figure.
            logged = [entry.as_time() for entry in logged]

        new, why = next_target(spec, current, logged, _streak(spec, logged, history))
        change = Change(spec, current, new, why)
        changes.append(change)
        if change.moved:
            reshaped |= _relay(layout, position, apply_block(block, spec, new))

    if reshaped:
        set_exercise_steps(
            payload, layout, _gaps_for(layout, spare, workout.rest_between)
        )

    return Plan(
        workout,
        payload,
        changes,
        shaped.warnings,
        notes=shaped.notes,
        rests=shaped.rests,
        sets=shaped.sets,
        skips=shaped.skips,
        structure=structure,
        gaps=gaps,
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
    shaped = _Shaping()

    blocks = list(iter_exercise_blocks(payload))
    layout = [block.outers for block in blocks]
    spare = steps_between(payload, blocks)
    reshaped = False

    for position, block in enumerate(blocks):
        step = block.step
        spec = _match(step, specs)
        if spec is None:
            continue

        current = block_target(block, spec)

        recounted = _refresh_block(block, spec, current, shaped)
        if recounted is not None:
            reshaped |= _relay(layout, position, recounted)

        new = targets.get(normalise(spec.garmin_name))
        if new is None:
            continue

        if current is None or current == new:
            continue

        # load_config rejects mismatched ranges, but a hand-edited Garmin
        # workout can still be out of step, so say so rather than hide it.
        if not spec.rep_low <= new.reps <= spec.rep_high:
            shaped.warnings.append(
                f"{spec.name}: synced target {new.reps} is outside this "
                f"workout's {spec.rep_low}-{spec.rep_high} range"
            )

        # The ramp travels with the target: two copies of one exercise that
        # disagreed about which sets are the hard ones would not be copies.
        reshaped |= _relay(layout, position, apply_block(block, spec, new))
        changes.append(Change(spec, current, new, f"synced from {source}"))

    if reshaped:
        set_exercise_steps(
            payload, layout, _gaps_for(layout, spare, workout.rest_between)
        )

    return Plan(
        workout,
        payload,
        changes,
        shaped.warnings,
        notes=shaped.notes,
        rests=shaped.rests,
        sets=shaped.sets,
        skips=shaped.skips,
    )


def executed_targets(
    workout: Workout, snapshot: list[dict[str, Any]]
) -> dict[str, Target]:
    """What each exercise was asked for in a past session, keyed for lookup.

    Matched to the config the same way a stored step is, so an exercise the
    watch logged under another name still finds its spec. Consecutive entries
    for one exercise are its two halves and are joined back together, which is
    what recovers a ramp.

    The weight is left at zero: the executed record does not carry one, and
    nothing that reads these targets needs it - whether the load changed is
    read off what was actually lifted.
    """
    specs = index_specs(workout.exercises)

    merged: list[tuple[ExerciseSpec, list[int]]] = []
    for entry in executed_exercises(snapshot):
        spec = specs.find(entry.name, entry.category)
        if spec is None:
            continue
        if merged and merged[-1][0] is spec:
            merged[-1][1].extend(entry.reps)
            continue
        merged.append((spec, list(entry.reps)))

    found: dict[str, Target] = {}
    for spec, asked in merged:
        base = min(asked)
        higher = base + spec.rep_step
        lead = sum(1 for reps in asked if reps == higher)
        found[normalise(spec.garmin_name)] = Target(base, 0.0, lead)
    return found


def decided_targets(plan: Plan) -> dict[str, Target]:
    """The targets that moved, keyed for lookup in another workout."""
    return {normalise(c.spec.garmin_name): c.new for c in plan.moved}
