"""Find, load and validate workouts.yaml.

All configuration lives in that file: the routine itself, the Garmin workout
ids, the weight increments, and the Garmin client settings. Nothing is
hardcoded elsewhere.

Where that file is depends on how the tool was installed, so it is searched
for rather than computed from this module's location - see `search_path()`.
"""

from __future__ import annotations

import os

from .domain.models import BODYWEIGHT, Config, ExerciseSpec, GarminSettings, Workout
from .errors import ConfigError
from .yamlio import dump, read, write

__all__ = [
    "load_config",
    "record_workout_id",
    "resolve_config",
    "search_path",
    "ConfigError",
]

CONFIG_NAME = "workouts.yaml"
EXAMPLE_NAME = "workouts.example.yaml"

#: Three directories above this module: src/workout/config.py -> the
#: repository root, when this is a checkout. Installed into site-packages the
#: same arithmetic points at a lib directory that holds nothing of ours, which
#: is what `_checkout_config` checks before believing it.
_CHECKOUT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

_REQUIRED = ("name", "garmin_name", "rep_low", "rep_high", "sets", "load")


def _xdg_config_home() -> str:
    return os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")


def _checkout_config() -> str | None:
    """Where a checkout keeps its config, if this is running from one.

    The shipped example sits beside it and is in version control, so its
    presence is what tells a clone from a site-packages directory that merely
    happens to be three levels up.
    """
    if not os.path.exists(os.path.join(_CHECKOUT_ROOT, EXAMPLE_NAME)):
        return None
    return os.path.join(_CHECKOUT_ROOT, CONFIG_NAME)


def search_path() -> list[str]:
    """Where to look for workouts.yaml, most specific first.

    In order: what `$WORKOUT_CONFIG` names, the working directory, the XDG
    config directory, and finally the checkout this module lives in - which is
    what makes `python -m workout` work from anywhere inside a clone. The
    checkout comes last so that it never shadows a config of the user's own,
    and is left out entirely when this is not a checkout, so that an installed
    copy does not offer a path inside site-packages as somewhere to look.
    """
    candidates = []
    named = os.environ.get("WORKOUT_CONFIG")
    if named:
        candidates.append(os.path.expanduser(named))
    candidates.append(os.path.join(os.getcwd(), CONFIG_NAME))
    candidates.append(os.path.join(_xdg_config_home(), "workout", CONFIG_NAME))
    checkout = _checkout_config()
    if checkout:
        candidates.append(checkout)
    return candidates


def _example_beside(path: str) -> str | None:
    """The shipped example, if there is one next to where the config would go."""
    example = os.path.join(os.path.dirname(path) or ".", EXAMPLE_NAME)
    return example if os.path.exists(example) else None


def resolve_config(explicit: str | None = None) -> str:
    """The config file to read: what was asked for, or the first one found."""
    if explicit:
        return explicit

    searched = search_path()
    for candidate in searched:
        if os.path.exists(candidate):
            return candidate

    # Nothing anywhere. Saying where it looked is the difference between a
    # user creating the file in the right place and guessing.
    where = "\n".join(f"    {candidate}" for candidate in searched)
    example = _example_beside(searched[0])
    hint = (
        f"Copy the example and edit it:\n    cp {example} {searched[0]}"
        if example
        else "Create one, or build a starting point from your Garmin account:\n"
        f"    workout import -o {searched[0]}"
    )
    raise ConfigError(f"No {CONFIG_NAME} found. Looked in:\n{where}\n{hint}")


def _identified(entry: dict, workout_id: str) -> dict:
    """The entry with its Garmin id set, and set where a reader expects it.

    Rebuilt rather than assigned into so that an id this tool has just learnt
    lands under `key`, where the hand-written ones are, rather than at the end
    of the entry underneath the exercises.
    """
    rebuilt: dict = {}
    for name, value in entry.items():
        if name == "garmin_workout_id":
            continue  # dropped here, re-added under `key` or below
        rebuilt[name] = value
        if name == "key":
            rebuilt["garmin_workout_id"] = workout_id
    rebuilt.setdefault("garmin_workout_id", workout_id)
    return rebuilt


def record_workout_id(path: str, key: str, workout_id: str) -> None:
    """Write a workout id that Garmin has just issued back into the config.

    Garmin decides the id, so the file has to learn it from a run rather than
    the other way round. It is the only thing this tool ever writes to
    workouts.yaml.

    The whole document is parsed, the one id set, and the whole thing written
    back. Comments and the user's own spacing do not survive that: it is the
    price of treating the file as data, and it buys one way of writing it
    rather than a line editor that has to reason about indentation.
    """
    data = read(path)

    workouts = data.get("workouts") if isinstance(data, dict) else None
    if not isinstance(workouts, list):
        raise ConfigError(f"{path}: expected a mapping with a 'workouts' key")

    for position, entry in enumerate(workouts):
        if isinstance(entry, dict) and entry.get("key") == key:
            workouts[position] = _identified(entry, workout_id)
            break
    else:
        # Refused rather than guessed at: writing the id into the wrong entry
        # would point two workouts at one Garmin workout.
        raise ConfigError(f"{path}: cannot find the workout entry for {key!r}")

    write(path, dump(data))


class Problems:
    """Everything wrong with the file, so that one run reports all of it.

    Validation used to stop at the first problem, which meant fixing a typo,
    running again, and finding the next one. Checks therefore record what they
    find and carry on; `raise_any` at the end decides whether the file loads.
    """

    def __init__(self) -> None:
        self.found: list[str] = []

    def add(self, detail: str) -> None:
        self.found.append(detail)

    def __bool__(self) -> bool:
        return bool(self.found)

    def raise_any(self) -> None:
        if not self.found:
            return
        if len(self.found) == 1:
            raise ConfigError(self.found[0])
        listed = "\n".join(f"  - {detail}" for detail in self.found)
        raise ConfigError(f"{len(self.found)} problems:\n{listed}")


def _build_exercise(
    raw: dict, steps: dict[str, float], where: str, problems: Problems
) -> ExerciseSpec | None:
    missing = [key for key in _REQUIRED if raw.get(key) is None]
    if missing:
        # Nothing else about this exercise can be judged without them.
        problems.add(f"{where}: exercise is missing {', '.join(missing)}")
        return None

    load = raw["load"]
    # An exercise may set its own step, e.g. the deadlift moves in bigger jumps
    # than the other barbell lifts. Otherwise the load type decides.
    declared_step = raw.get("weight_step")
    if declared_step is None:
        if load != BODYWEIGHT and load not in steps:
            problems.add(
                f"{where}: exercise {raw['name']!r} has load {load!r}, "
                f"which has no entry in settings.weight_steps"
            )
        weight_step = float(steps.get(load, 0.0))
    else:
        weight_step = float(declared_step)
        if weight_step <= 0 and load != BODYWEIGHT:
            problems.add(
                f"{where}: {raw['name']!r} has a weight_step of {weight_step:g}, "
                f"which would never progress"
            )

    if raw["rep_low"] >= raw["rep_high"]:
        problems.add(f"{where}: {raw['name']!r} has rep_low >= rep_high")

    # Not `or 1`: that would silently turn an explicit 0 into 1.
    declared = raw.get("rep_step")
    rep_step = 1 if declared is None else int(declared)
    if rep_step < 1:
        problems.add(f"{where}: {raw['name']!r} has rep_step below 1")
        # Kept usable so the rest of the file is still checked. The load
        # fails on the recorded problem either way.
        rep_step = 1

    start_weight = float(raw.get("start_weight") or 0.0)
    if start_weight < 0:
        problems.add(f"{where}: {raw['name']!r} has a negative start_weight")
        start_weight = 0.0

    return ExerciseSpec(
        name=raw["name"],
        garmin_name=raw["garmin_name"],
        rep_low=int(raw["rep_low"]),
        rep_high=int(raw["rep_high"]),
        sets=int(raw["sets"]),
        load=load,
        # Bodyweight moves never gain load, so their step is irrelevant.
        weight_step=weight_step,
        garmin_category=raw.get("garmin_category"),
        rep_step=rep_step,
        rest=int(raw.get("rest", 0)),
        unit=raw.get("unit", "reps"),
        notes=raw.get("notes"),
        start_weight=start_weight,
    )


def _build_workout(
    entry: dict, steps: dict[str, float], path: str, problems: Problems
) -> Workout | None:
    key = entry.get("key")
    if not key:
        # Without a key there is nothing to label its exercises with, so
        # there is no useful way to report anything else about this one.
        problems.add(f"{path}: a workout is missing its 'key'")
        return None

    workout_id = entry.get("garmin_workout_id")

    exercises = []
    for raw in entry.get("exercises") or []:
        spec = _build_exercise(raw, steps, f"{path}:{key}", problems)
        if spec is not None:
            exercises.append(spec)

    # No id means "Garmin does not have this one yet", which is a workout to
    # create rather than a mistake - but only if there is something to create.
    if not workout_id and not exercises:
        problems.add(
            f"{path}: {key} has no 'garmin_workout_id' and no exercises, "
            f"so there is nothing to find and nothing to create"
        )
        return None

    # Not `or 0`: an explicit 0 is a rest of no length, which is a different
    # thing from having no opinion about the rest between exercises.
    declared = entry.get("rest_between_exercises")
    rest_between = None if declared is None else int(declared)
    if rest_between is not None and rest_between < 0:
        problems.add(f"{path}: {key} has a negative rest_between_exercises")
        rest_between = None

    return Workout(
        key=key,
        garmin_workout_id=str(workout_id) if workout_id else None,
        activity_prefixes=[p.lower() for p in entry.get("activity_prefixes") or []],
        exercises=exercises,
        rest_between=rest_between,
    )


def _check_shared(config: Config, path: str, problems: Problems) -> None:
    """A shared exercise must be programmed identically everywhere.

    Otherwise a target synced out of one workout could land outside another
    workout's range.
    """
    for garmin_name in config.shared_exercises():
        specs = [
            spec
            for workout in config.workouts.values()
            for spec in workout.exercises
            if spec.garmin_name == garmin_name
        ]
        ranges = {(s.rep_low, s.rep_high, s.rep_step) for s in specs}
        if len(ranges) > 1:
            problems.add(
                f"{path}: {garmin_name} appears in several workouts with "
                f"different rep ranges {sorted(ranges)}; a synced target could "
                f"fall outside one of them"
            )


def load_config(path: str | None = None) -> Config:
    """Read workouts.yaml, validating as we go.

    With no path, the file is searched for: see `resolve_config`.
    """
    path = resolve_config(path)
    if not os.path.exists(path):
        # Only reachable for a path that was asked for by name, since a
        # resolved one exists by construction.
        raise ConfigError(f"{path} does not exist")

    data = read(path)
    if not isinstance(data, dict) or "workouts" not in data:
        raise ConfigError(f"{path}: expected a mapping with a 'workouts' key")
    if not isinstance(data["workouts"], list):
        raise ConfigError(f"{path}: 'workouts' should be a list of workouts")

    settings = data.get("settings") or {}
    steps = settings.get("weight_steps") or {}
    garmin_raw = settings.get("garmin") or {}
    defaults = GarminSettings()
    garmin = GarminSettings(
        token_store=os.path.expanduser(
            garmin_raw.get("token_store") or defaults.token_store
        ),
        activity_search_limit=int(
            garmin_raw.get("activity_search_limit") or defaults.activity_search_limit
        ),
        dump_dir=os.path.expanduser(garmin_raw.get("dump_dir") or defaults.dump_dir),
    )

    problems = Problems()
    workouts: dict[str, Workout] = {}
    for entry in data["workouts"]:
        workout = _build_workout(entry, steps, path, problems)
        if workout is None:
            continue
        if workout.key in workouts:
            problems.add(f"{path}: duplicate workout key {workout.key!r}")
            continue
        workouts[workout.key] = workout

    # Only worth saying when the file really is empty; when every workout in it
    # failed, the reason each one failed is the more useful thing to report.
    if not workouts and not problems:
        problems.add(f"{path}: no workouts defined")

    config = Config(workouts=workouts, garmin=garmin, path=path)
    _check_shared(config, path, problems)

    problems.raise_any()
    return config
