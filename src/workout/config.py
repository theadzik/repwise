"""Find, load and validate workouts.yaml.

All configuration lives in that file: the routine itself, the Garmin workout
ids, the weight increments, and the Garmin client settings. Nothing is
hardcoded elsewhere.

Where that file is depends on how the tool was installed, so it is searched
for rather than computed from this module's location - see `search_path()`.
"""

from __future__ import annotations

import os

import yaml

from .domain.models import BODYWEIGHT, Config, ExerciseSpec, GarminSettings, Workout
from .errors import ConfigError

__all__ = ["load_config", "resolve_config", "search_path", "ConfigError"]

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


def _build_exercise(raw: dict, steps: dict[str, float], where: str) -> ExerciseSpec:
    missing = [key for key in _REQUIRED if raw.get(key) is None]
    if missing:
        raise ConfigError(f"{where}: exercise is missing {', '.join(missing)}")

    load = raw["load"]
    # An exercise may set its own step, e.g. the deadlift moves in bigger jumps
    # than the other barbell lifts. Otherwise the load type decides.
    declared_step = raw.get("weight_step")
    if declared_step is None:
        if load != BODYWEIGHT and load not in steps:
            raise ConfigError(
                f"{where}: exercise {raw['name']!r} has load {load!r}, "
                f"which has no entry in settings.weight_steps"
            )
        weight_step = float(steps.get(load, 0.0))
    else:
        weight_step = float(declared_step)
        if weight_step <= 0 and load != BODYWEIGHT:
            raise ConfigError(
                f"{where}: {raw['name']!r} has a weight_step of {weight_step:g}, "
                f"which would never progress"
            )

    if raw["rep_low"] >= raw["rep_high"]:
        raise ConfigError(f"{where}: {raw['name']!r} has rep_low >= rep_high")

    # Not `or 1`: that would silently turn an explicit 0 into 1.
    declared = raw.get("rep_step")
    rep_step = 1 if declared is None else int(declared)
    if rep_step < 1:
        raise ConfigError(f"{where}: {raw['name']!r} has rep_step below 1")

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
        video=raw.get("video"),
    )


def _build_workout(entry: dict, steps: dict[str, float], path: str) -> Workout:
    key = entry.get("key")
    if not key:
        raise ConfigError(f"{path}: a workout is missing its 'key'")
    if not entry.get("garmin_workout_id"):
        raise ConfigError(f"{path}: {key} is missing 'garmin_workout_id'")

    return Workout(
        key=key,
        garmin_workout_id=str(entry["garmin_workout_id"]),
        activity_prefixes=[p.lower() for p in entry.get("activity_prefixes") or []],
        exercises=[
            _build_exercise(raw, steps, f"{path}:{key}")
            for raw in entry.get("exercises") or []
        ],
    )


def _check_shared(config: Config, path: str) -> None:
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
            raise ConfigError(
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

    # A file that cannot be read or parsed is a configuration problem like any
    # other, so it leaves here as one rather than as a traceback from yaml.
    try:
        with open(path) as fh:
            data = yaml.safe_load(fh)
    except OSError as exc:
        raise ConfigError(f"{path} could not be read: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc

    if not isinstance(data, dict) or "workouts" not in data:
        raise ConfigError(f"{path}: expected a mapping with a 'workouts' key")

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

    workouts: dict[str, Workout] = {}
    for entry in data["workouts"]:
        workout = _build_workout(entry, steps, path)
        if workout.key in workouts:
            raise ConfigError(f"{path}: duplicate workout key {workout.key!r}")
        workouts[workout.key] = workout

    if not workouts:
        raise ConfigError(f"{path}: no workouts defined")

    config = Config(workouts=workouts, garmin=garmin)
    _check_shared(config, path)
    return config
