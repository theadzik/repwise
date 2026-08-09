"""Domain objects describing a routine.

These are plain data: how an exercise is programmed, which Garmin workout it
lives in, and the settings that govern progression. Nothing here reads a file
or talks to Garmin.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field

BODYWEIGHT = "bodyweight"


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
    #: The share of your bodyweight this movement carries: 1.0 for a calf raise
    #: or a weighted pull-up, 0 for anything you lie or sit down to do. Never
    #: guessed from the category - a lat pull-down is categorised `PULL_UP` and
    #: carries none of you - so the default is to count only the stored weight.
    #: Read by `check` alone; see `domain/effort.py`.
    bodyweight_factor: float = 0.0

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
    dump_dir: str = "."


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
        """Garmin names that appear in more than one workout."""
        seen: dict[str, int] = {}
        for workout in self.workouts.values():
            for spec in workout.exercises:
                seen[spec.garmin_name] = seen.get(spec.garmin_name, 0) + 1
        return {name for name, count in seen.items() if count > 1}
