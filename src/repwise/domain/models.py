"""Domain objects describing a routine.

These are plain data: how an exercise is programmed, which Garmin workout it
lives in, and the settings that govern progression. Nothing here reads a file
or talks to Garmin.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field

BODYWEIGHT = "bodyweight"


@dataclass(frozen=True)
class LoadTier:
    """One rack in a group: how light and heavy it goes, and how it steps.

    A load type is a *group* of these, in ascending order. Most groups hold
    exactly one - a barbell is a barbell, and the plates run out long after you
    do - and that single tier is what every config written before groups
    existed resolves to, which is why nothing had to change to keep working.

    Two kinds of equipment need more than one, and they are different problems
    wearing the same word:

    **Racks.** Two tiers with disjoint ranges: the fixed dumbbells that run
    1-10 kg in ones, and the rack next to them that starts at 12 and goes up in
    twos. Exactly one tier holds any given weight, so the tier is chosen by
    what is on the bar, and topping one out moves to the next.

    **Increments.** One tier whose `steps` names several: a cable stack that
    takes 1.25 kg micro-plates as readily as a 5 kg pin move. Every step is
    available at every weight, so the choice is not geometry but effort - see
    `chosen_step` in `domain/effort.py`.

    Both fall out of the same two fields, which is why they are one class: a
    tier says what loads exist between its ends, and a group says which tiers
    exist. Nothing here knows why you own two racks.
    """

    #: The lightest this tier goes. Every rack has a bottom, and a deload that
    #: does not know it prescribes a weight you have no way to make up.
    minimum: float
    #: The heaviest, or None where the equipment outlasts you.
    maximum: float | None
    #: The increments this tier can express, ascending. One is the ordinary
    #: case; several means the load can be micro-plated and the right size of
    #: jump depends on how heavy it already is.
    steps: tuple[float, ...]

    @property
    def step(self) -> float:
        """The smallest increment, which is what a single-step tier states."""
        return self.steps[0] if self.steps else 0.0

    def holds(self, weight: float) -> bool:
        """Whether this tier can express `weight` at all."""
        if weight < self.minimum:
            return False
        return self.maximum is None or weight <= self.maximum


@dataclass(frozen=True)
class ExerciseSpec:
    """One exercise as declared in workouts.yaml."""

    name: str
    garmin_name: str
    rep_low: int
    rep_high: int
    sets: int
    load: str
    weight_step: float
    garmin_category: str | None = None
    rep_step: int = 1
    rest: int = 0
    unit: str = "reps"
    #: Whatever the user wants to keep beside this exercise: a cue, a link, a
    #: reminder. Read by nobody - not this tool, not Garmin. Distinct from
    #: `note` below, which this tool writes to the watch.
    notes: str | None = None
    #: The load a step starts at when this tool has to create it. Only ever
    #: read for an exercise Garmin does not hold yet; progression owns the
    #: weight from the first session onward.
    start_weight: float = 0.0
    #: The lightest this exercise can be loaded: the smallest bar on the rack,
    #: the lightest pair of dumbbells, the top plate of the stack. A deload
    #: stops here rather than prescribing a weight that does not exist.
    min_weight: float = 0.0
    #: The heaviest this exercise can be loaded: the largest pair of dumbbells
    #: you own, the last plate in the bag, the bottom of the stack. Topping out
    #: the rep range stops here rather than prescribing a weight you cannot
    #: load. `None` is no ceiling, and is the right default - a gym rack runs
    #: out long after you do, and it is home equipment that really ends.
    max_weight: float | None = None
    #: The share of your bodyweight this movement carries: 1.0 for a calf raise
    #: or a weighted pull-up, 0 for anything you lie or sit down to do. Never
    #: guessed from the category - a lat pull-down is categorised `PULL_UP` and
    #: carries none of you - so the default is to count only the stored weight.
    #: Read by `check` alone; see `domain/effort.py`.
    bodyweight_factor: float = 0.0
    #: Whether a hit after a stall may move only some of the sets. On - the
    #: default - a target can ramp, `8+2` being eight reps with two of the sets
    #: asked for nine. Off, every set moves together however long the stall,
    #: and a deload steps the whole target down rather than one set of it.
    #: Declared once as settings.partial_progression and resolved onto every
    #: exercise, the way a weight step is.
    partial_progression: bool = True
    #: The equipment this exercise is loaded on, ascending. Empty means the
    #: load type said nothing a single `weight_step`, `min_weight` and
    #: `max_weight` could not, which is every config written before groups
    #: existed; `tier_span` builds the one tier those three describe, so
    #: nothing downstream has to ask which kind of file it came from.
    tiers: tuple[LoadTier, ...] = ()

    @property
    def tier_span(self) -> tuple[LoadTier, ...]:
        """The tiers, or the single one this exercise's own bounds describe."""
        if self.tiers:
            return self.tiers
        return (LoadTier(self.min_weight, self.max_weight, (self.weight_step,)),)

    def tier_for(self, weight: float) -> LoadTier:
        """Which rack `weight` is on.

        A weight between two racks - 11 kg, with one ending at 10 and the next
        starting at 12 - belongs to neither, and is answered with the heavier,
        because a load above a rack's ceiling got there by going up. The same
        answer serves a weight below every rack, where the lightest is the only
        one that could ever hold it.
        """
        span = self.tier_span
        for tier in span:
            if tier.holds(weight):
                return tier
        above = [tier for tier in span if tier.minimum > weight]
        return above[0] if above else span[-1]

    @property
    def bodyweight(self) -> bool:
        """True when there is no external load to progress."""
        return self.load == BODYWEIGHT

    @property
    def time_based(self) -> bool:
        return self.unit == "seconds"

    @property
    def note(self) -> str:
        """How this exercise is programmed, for the step's notes field.

        The target already tells you what to do today; this says what you are
        working towards and what happens when you get there. Kept to one short
        line, because it is read on a watch mid-set.
        """
        span = f"{self.rep_low}-{self.rep_high} {'s' if self.time_based else 'reps'}"
        if self.rep_step != 1:
            span += f" by {self.rep_step}"
        load = "bodyweight" if self.bodyweight else f"+{self.weight_step:g} kg"
        return f"{span} | {load}"


@dataclass(frozen=True)
class Workout:
    """One workout: its Garmin id, how to spot its activities, its exercises.

    The id is what Garmin knows this workout as, and the one thing here that
    the user does not choose. It is absent until Garmin has been told about the
    workout, which is what makes a config entry declaring a workout that does
    not exist yet expressible at all.
    """

    key: str
    garmin_workout_id: str | None = None
    activity_prefixes: list[str] = field(default_factory=list)
    exercises: list[ExerciseSpec] = field(default_factory=list)
    #: Seconds to rest between exercises, or None to leave Garmin's own steps
    #: alone. One setting for the whole workout: it is a property of how the
    #: session is run rather than of any exercise in it.
    rest_between: int | None = None

    def claims(self, activity_name: str) -> bool:
        """Whether an activity with this name was a session of this workout.

        The one place the rule lives, read in both directions: finding the
        workout an activity belongs to, and finding a workout's activities.
        """
        name = activity_name.lower()
        return any(name.startswith(prefix) for prefix in self.activity_prefixes)


@dataclass(frozen=True)
class GarminSettings:
    """Everything about talking to Garmin that a user might want to change."""

    #: Beside the config, so that one directory is everything this tool owns.
    #: What is kept there is a bearer credential for the account, which is why
    #: `garmin/client.py` has an opinion about who can read it. `config.py`
    #: resolves `$XDG_CONFIG_HOME` when a real run names no store of its own;
    #: this literal is the same directory for anyone who has not moved it.
    token_store: str = "~/.config/repwise"
    activity_search_limit: int = 50
    #: Under the XDG data home rather than the cache home: once Garmin's search
    #: window has moved past a session, the copy here is the only one left, and
    #: a cache directory is somewhere anything may be deleted at any time.
    #: `config.py` resolves `$XDG_DATA_HOME`; this literal is the same
    #: directory for anyone who has not moved it.
    dump_dir: str = "~/.local/share/repwise/dumps"
    #: Whether the payloads already in `dump_dir` may be read instead of asked
    #: for again. Off by default: a session Garmin holds is the truth, and a
    #: tool that quietly prefers its own copy of it should be asked for, not
    #: assumed. On, a run also files every session it sees, so the directory
    #: fills itself. What makes that safe to trust is that Garmin's own totals
    #: for a session are checked against the copy on disk - see `dumps.py`.
    activity_caching: bool = False


@dataclass(frozen=True)
class Config:
    """A parsed workouts.yaml."""

    workouts: dict[str, Workout]
    garmin: GarminSettings = field(default_factory=GarminSettings)
    #: Your weight in kg, when you would rather state it than have it read from
    #: your Garmin weigh-ins. Unset - the normal case - means ask Garmin, which
    #: keeps it current without anyone editing a file. Only ever an input to
    #: `check`; no target depends on it.
    bodyweight: float | None = None
    #: The file this was read from. Carried so that a use case which learns
    #: something the file should record - a workout id Garmin has just issued -
    #: can write it back without the CLI having to pass the path separately.
    path: str = ""

    def __getitem__(self, key: str) -> Workout:
        return self.workouts[key]

    def __iter__(self) -> Iterator[Workout]:
        return iter(self.workouts.values())

    def shared_exercises(self) -> set[str]:
        """Garmin names that appear in more than one workout on one load.

        The load counts as much as the name. The same movement performed on
        different equipment is two exercises that happen to be called the same
        thing - the seated calf raise on the gym machine and the one done with
        a pair of dumbbells at home - and a weight decided on one of them is
        not a weight the other can be asked for.
        """
        seen: dict[tuple[str, str], int] = {}
        for workout in self.workouts.values():
            for spec in workout.exercises:
                key = (spec.garmin_name, spec.load)
                seen[key] = seen.get(key, 0) + 1
        return {name for (name, _), count in seen.items() if count > 1}
