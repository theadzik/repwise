"""Load and validate workouts.yaml.

All configuration lives in that file: the routine itself, the Garmin workout
ids, the weight increments, and the Garmin client settings. Nothing is
hardcoded elsewhere.
"""

from __future__ import annotations

import os

import yaml

from .domain.models import BODYWEIGHT, Config, ExerciseSpec, GarminSettings, Workout
from .errors import ConfigError

__all__ = ["load_config", "ConfigError", "DEFAULT_CONFIG", "EXAMPLE_CONFIG"]

#: Repository root, two levels above this package.
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_CONFIG = os.path.join(_ROOT, "workouts.yaml")
EXAMPLE_CONFIG = os.path.join(_ROOT, "workouts.example.yaml")

_REQUIRED = ("name", "garmin_name", "rep_low", "rep_high", "sets", "load")


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


def load_config(path: str = DEFAULT_CONFIG) -> Config:
    """Read workouts.yaml, validating as we go."""
    if not os.path.exists(path):
        # workouts.yaml is deliberately not in version control, so a fresh
        # checkout has only the example.
        if path == DEFAULT_CONFIG and os.path.exists(EXAMPLE_CONFIG):
            raise ConfigError(
                f"{path} does not exist yet. Copy the example and edit it:\n"
                f"    cp {os.path.basename(EXAMPLE_CONFIG)} "
                f"{os.path.basename(path)}"
            )
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
