"""Domain objects describing a routine.

These are plain data: how an exercise is programmed, which Garmin workout it
lives in, and the settings that govern progression. Nothing here reads a file
or talks to Garmin.
"""

from __future__ import annotations

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
    video: str | None = None

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
    """One workout: its Garmin id, how to spot its activities, its exercises."""

    key: str
    garmin_workout_id: str
    activity_prefixes: list[str] = field(default_factory=list)
    exercises: list[ExerciseSpec] = field(default_factory=list)


@dataclass(frozen=True)
class GarminSettings:
    """Everything about talking to Garmin that a user might want to change."""

    token_store: str = "~/.garminconnect"
    activity_search_limit: int = 25
    dump_dir: str = "."


@dataclass(frozen=True)
class Config:
    """A parsed workouts.yaml."""

    workouts: dict[str, Workout]
    garmin: GarminSettings = field(default_factory=GarminSettings)

    def __getitem__(self, key: str) -> Workout:
        return self.workouts[key]

    def __iter__(self):
        return iter(self.workouts.values())

    def shared_exercises(self) -> set[str]:
        """Garmin names that appear in more than one workout."""
        seen: dict[str, int] = {}
        for workout in self.workouts.values():
            for spec in workout.exercises:
                seen[spec.garmin_name] = seen.get(spec.garmin_name, 0) + 1
        return {name for name, count in seen.items() if count > 1}
