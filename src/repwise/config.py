"""Find, load and validate workouts.yaml.

All configuration lives in that file: the routine itself, the Garmin workout
ids, the weight increments, and the Garmin client settings. Nothing is
hardcoded elsewhere.

Where that file is depends on how the tool was installed, so it is searched
for rather than computed from this module's location - see `search_path()`.
"""

import logging
import os
from dataclasses import dataclass
from typing import Any

from .domain.models import BODYWEIGHT, Config, ExerciseSpec, GarminSettings, Workout
from .errors import ConfigError
from .yamlio import dump, read, write

__all__ = [
    "load_config",
    "record_workout_id",
    "resolve_config",
    "search_path",
    "default_dump_dir",
    "default_token_store",
    "ConfigError",
]

logger = logging.getLogger(__name__)

CONFIG_NAME = "workouts.yaml"
EXAMPLE_NAME = "workouts.example.yaml"

#: The one directory this tool owns, under the XDG config home: where an
#: installed copy looks for workouts.yaml, and where the Garmin tokens and the
#: exercise catalog go unless the file names somewhere else.
APP_DIR = "repwise"

#: Three directories above this module: src/repwise/config.py -> the
#: repository root, when this is a checkout. Installed into site-packages the
#: same arithmetic points at a lib directory that holds nothing of ours, which
#: is what `_checkout_config` checks before believing it.
_CHECKOUT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

_REQUIRED = ("name", "garmin_name", "rep_low", "rep_high", "sets", "load")


@dataclass(frozen=True)
class LoadType:
    """One named way of loading: how it steps, and how light and heavy it goes.

    Named by the user rather than drawn from a list of equipment types, because
    one word does not tell one rack from another: the dumbbells at home start
    at 1 kg and stop at 10, the pair in the gym start at 2 and run to 40. Both
    are dumbbells, and a deload can only be told what exists to prescribe if
    they are declared apart. So each rack is named under the top-level `load`,
    and an exercise's own `load` says which one it is performed on.

    All three are properties of the equipment rather than of any one exercise -
    a rack goes up in ones, starts at one and ends at whatever the heaviest
    pair is - so they are declared once and only overridden where an exercise
    really differs.
    """

    #: kg added when a rep range is topped out. Required: a load type that
    #: cannot say how it steps cannot progress anything.
    step: float
    #: The lightest this equipment goes: the smallest bar on the rack, the
    #: lightest pair, the top plate of the stack. Required, because every rack
    #: has a bottom, and a deload that does not know it prescribes a weight you
    #: have no way to make up.
    minimum: float
    #: The heaviest it goes, or None where it outlasts you - a gym's stack, a
    #: bar with more plates left in the rack. Only equipment you own runs out.
    maximum: float | None = None


#: The three maps the top-level `load` replaced, each keyed by load type.
_MOVED = ("weight_steps", "min_weights", "max_weights")


def _reject_moved_settings(settings: dict, path: str) -> None:
    """Say what became of the three maps that the top-level `load` replaced.

    They were keyed by a fixed idea of equipment - one `dumbbell` entry for
    every dumbbell you own - which is the thing named load types exist to
    undo.
    Loading such a file halfway would quietly drop its floors, ceilings and
    steps, so it is refused outright with the shape to write instead.
    """
    found = [key for key in _MOVED if settings.get(key) is not None]
    if not found:
        return

    named = ", ".join(f"settings.{key}" for key in found)
    raise ConfigError(
        f"{path}: {named} {'has' if len(found) == 1 else 'have'} been replaced "
        f"by a top-level 'load' key. Each named load type states its own step "
        f"and how light and heavy it goes:\n"
        f"    load:\n"
        f"      barbell:\n"
        f"        min: 12.0\n"
        f"        step: 2.5\n"
        f"      home_dumbbell:\n"
        f"        min: 1.0\n"
        f"        max: 10.0\n"
        f"        step: 1.0\n"
        f"An exercise's 'load' then names one of them."
    )


def _xdg_config_home() -> str:
    return os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")


def _xdg_data_home() -> str:
    return os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")


def default_token_store() -> str:
    """Where the Garmin tokens go when the config does not say.

    The same directory an installed copy keeps its config in, so that what this
    tool owns is one directory rather than a config here and a dot-directory of
    Garmin's naming somewhere else. `GarminSettings` states the same path as a
    plain default, for anything constructing one without a file behind it; this
    is the one that honours `$XDG_CONFIG_HOME`, so that a user who moved their
    config does not find their tokens left behind in `~/.config`.
    """
    return os.path.join(_xdg_config_home(), APP_DIR)


def default_dump_dir() -> str:
    """Where `fetch` writes, and what `activity_caching` reads back.

    Under `$XDG_DATA_HOME` rather than the cache home, and for the reason
    `activity_caching` exists at all: a session that has scrolled past
    `activity_search_limit` is only on disk here, and a cache directory is
    somewhere anything may be deleted at any time.

    `GarminSettings` states the same path as a plain default, for anything
    constructing one without a file behind it; this is the one that honours
    the environment, exactly as `default_token_store()` does.
    """
    return os.path.join(_xdg_data_home(), APP_DIR, "dumps")


def _flag(declared: Any, key: str, default: bool) -> bool:
    """A yes-or-no setting, read as one rather than coerced into one.

    `key` is the whole dotted path, since these live at more than one level of
    the file and a message naming the wrong one is worse than no message.

    YAML has booleans, so a setting that wants one should insist: `bool()`
    would read the string `"false"` as true and turn a feature on for someone
    who wrote it in quotes, which is the kind of mistake a config file should
    not be able to make quietly.

    Absent and null both mean the default, because neither of them is an
    answer - a key written with nothing after it is a key someone has not
    finished writing, not a `false`.
    """
    if declared is None:
        return default
    if not isinstance(declared, bool):
        raise ConfigError(
            f"settings.{key} should be true or false, not "
            f"{declared!r}. Unquoted, so that YAML reads it as a boolean."
        )
    return declared


def _warn_if_wandering(garmin: GarminSettings) -> None:
    """Say when the cache has been pointed at a directory that moves.

    A relative `dump_dir` is resolved against the working directory, so it
    names a different place for every place you run from. As somewhere to drop
    dumps that is untidy and no worse. As a cache it does not work at all: each
    directory starts empty, downloads the whole search limit to fill itself,
    and keeps its own copy of a history that is supposed to be one thing.

    Warned rather than refused. It is a coherent thing to ask for if you only
    ever run from one directory, and this is not the moment to decide that for
    someone.
    """
    if not garmin.activity_caching or os.path.isabs(garmin.dump_dir):
        return

    logger.warning(
        f"settings.garmin.activity_caching is on, but dump_dir is "
        f"{garmin.dump_dir!r}, which is relative to wherever repwise is run "
        f"from - so this is a separate, empty cache for every such directory."
    )
    logger.warning(
        f"Remove the setting to use the default, {default_dump_dir()}, or name "
        f"an absolute path of your own under settings.garmin."
    )


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

    In order: what `$REPWISE_CONFIG` names, the working directory, the XDG
    config directory, and finally the checkout this module lives in - which is
    what makes `python -m repwise` work from anywhere inside a clone. The
    checkout comes last so that it never shadows a config of the user's own,
    and is left out entirely when this is not a checkout, so that an installed
    copy does not offer a path inside site-packages as somewhere to look.
    """
    candidates = []
    named = os.environ.get("REPWISE_CONFIG")
    if named:
        candidates.append(os.path.expanduser(named))
    candidates.append(os.path.join(os.getcwd(), CONFIG_NAME))
    candidates.append(os.path.join(_xdg_config_home(), APP_DIR, CONFIG_NAME))
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
        f"    repwise import -o {searched[0]}"
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


def _load_types(declared: Any, path: str, problems: Problems) -> dict[str, LoadType]:
    """The named ways of loading an exercise, from the top-level `load`.

    Every name here is the user's own - `barbell`, `gym_dumbbell`,
    `home_dumbbell`, whatever tells one rack from another. Only `bodyweight` is
    spoken for: an exercise loaded that way carries no equipment, so it draws
    on nothing declared here.

    Names are lower-cased on the way in, and an exercise's `load` is lower-cased
    to match, so `Gym_Dumbbell` and `gym_dumbbell` are one rack however either
    end happens to be typed. Which is also why two names differing only in case
    are refused: they would otherwise be one entry silently overwriting the
    other, and only one of the two racks would survive.

    A broken entry is recorded and then kept, in the shape it can be, so that
    the exercises using it are still checked rather than each being reported a
    second time as naming a load type that does not exist.
    """
    if declared is None:
        return {}
    if not isinstance(declared, dict):
        problems.add(f"{path}: 'load' should be a mapping of name to min/max/step")
        return {}

    types: dict[str, LoadType] = {}
    #: Lower-cased name -> the name as written, for a message that can be
    #: found in the file.
    written: dict[str, str] = {}
    for name, entry in declared.items():
        where = f"{path}: load.{name}"
        key = str(name).lower()
        if key == BODYWEIGHT:
            problems.add(
                f"{where} is a reserved name: an exercise loaded {BODYWEIGHT!r} "
                f"has no equipment, so it draws on no load type of yours"
            )
            continue
        if key in written:
            problems.add(
                f"{where} and load.{written[key]} are one name: load types are "
                f"matched in lower case, so only one of the two would survive"
            )
            continue
        written[key] = str(name)
        if not isinstance(entry, dict):
            problems.add(f"{where} should state min, step and optionally max")
            continue

        missing = [key for key in ("min", "step") if entry.get(key) is None]
        if missing:
            problems.add(f"{where} is missing {', '.join(missing)}")

        minimum = float(entry.get("min") or 0.0)
        if minimum < 0:
            problems.add(f"{where} has a negative min")
            minimum = 0.0

        step = float(entry.get("step") or 0.0)
        if step < 0:
            problems.add(f"{where} has a negative step")
            step = 0.0

        declared_maximum = entry.get("max")
        maximum = None if declared_maximum is None else float(declared_maximum)
        if maximum is not None and maximum < minimum:
            problems.add(
                f"{where} has a max of {maximum:g} below its min of "
                f"{minimum:g}, so no load fits between them"
            )
            maximum = None

        types[key] = LoadType(step=step, minimum=minimum, maximum=maximum)

    return types


def _bounds(
    raw: dict, load_type: LoadType | None, where: str, problems: Problems
) -> tuple[float, float | None]:
    """How light and how heavy this exercise may be loaded.

    Resolved together because they only mean anything against each other: a
    ceiling under its own floor leaves progression choosing between two
    impossible loads, and that is only visible once both are known.

    The two ends default differently, and deliberately. The floor comes from
    the load type the exercise names, which always states one; nothing but a
    bodyweight movement, which names none, reaches the zero here. No
    ceiling is `None` rather than zero, because zero is a real maximum -
    nothing may be added at all - and equipment that does not run out before
    you do is the common case.

    Anything contradictory is dropped rather than honoured. The file will not
    load either way - `problems` sees to that - so the only question is which
    spec the rest of the checks get to run against.
    """
    declared_minimum = raw.get("min_weight")
    if declared_minimum is None:
        minimum = load_type.minimum if load_type else 0.0
    else:
        minimum = float(declared_minimum)
    if minimum < 0:
        problems.add(f"{where}: {raw['name']!r} has a negative min_weight")
        minimum = 0.0

    declared_maximum = raw.get("max_weight")
    if declared_maximum is None:
        ceiling = load_type.maximum if load_type else None
        if ceiling is None:
            return minimum, None
        maximum = ceiling
    else:
        maximum = float(declared_maximum)

    if maximum < 0:
        problems.add(f"{where}: {raw['name']!r} has a negative max_weight")
        return minimum, None
    if maximum < minimum:
        problems.add(
            f"{where}: {raw['name']!r} has a max_weight of {maximum:g} below "
            f"its min_weight of {minimum:g}, so no load fits between them"
        )
        return minimum, None

    return minimum, maximum


def _bodyweight_factor(raw: dict, where: str, problems: Problems) -> float:
    """The share of the lifter this exercise carries, if it says.

    Opt-in and defaulting to none, so every config written before it existed
    keeps its meaning exactly. Never inferred from the category: a lat
    pull-down is filed under `PULL_UP` and carries none of you, and guessing
    would be wrong on the first one that mattered.

    Read only by `check` - see `domain/effort.py`.
    """
    factor = float(raw.get("bodyweight_factor") or 0.0)
    if not 0.0 <= factor <= 1.0:
        problems.add(
            f"{where}: {raw['name']!r} has a bodyweight_factor of {factor:g}; "
            f"it is the share of you that the movement carries, so it belongs "
            f"between 0 and 1"
        )
        factor = 0.0

    return factor


def _build_exercise(
    raw: dict,
    types: dict[str, LoadType],
    where: str,
    problems: Problems,
    *,
    partial_progression: bool = True,
) -> ExerciseSpec | None:
    missing = [key for key in _REQUIRED if raw.get(key) is None]
    if missing:
        # Nothing else about this exercise can be judged without them.
        problems.add(f"{where}: exercise is missing {', '.join(missing)}")
        return None

    # Lower-cased to match the load types, which are keyed that way, and stored
    # lower-cased so that everything reading a load downstream - the bodyweight
    # test, the sync that refuses to cross equipment - compares like with like.
    load = str(raw["load"]).lower()
    # Bodyweight is the one load that names no load type: there is no
    # equipment to describe, and nothing to add to it.
    load_type = None if load == BODYWEIGHT else types.get(load)
    if load != BODYWEIGHT and load_type is None:
        known = ", ".join(sorted(types)) or "none"
        problems.add(
            f"{where}: exercise {raw['name']!r} has load {load!r}, which is "
            f"not among the load types defined at the top level ({known})"
        )

    # An exercise may set its own step, e.g. the deadlift moves in bigger jumps
    # than the other barbell lifts. Otherwise the load type it names decides.
    declared_step = raw.get("weight_step")
    if declared_step is None:
        weight_step = load_type.step if load_type else 0.0
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

    min_weight, max_weight = _bounds(raw, load_type, where, problems)
    if max_weight is not None and start_weight > max_weight:
        # `start_weight` is read only when the step is created, which is the
        # one moment nothing else could catch this: no session has been logged
        # yet, so progression never gets a chance to refuse the weight.
        problems.add(
            f"{where}: {raw['name']!r} would start at {start_weight:g} kg, "
            f"above its own max_weight of {max_weight:g}"
        )
        max_weight = None

    bodyweight_factor = _bodyweight_factor(raw, where, problems)

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
        min_weight=min_weight,
        max_weight=max_weight,
        bodyweight_factor=bodyweight_factor,
        partial_progression=partial_progression,
    )


def _build_workout(
    entry: dict,
    types: dict[str, LoadType],
    path: str,
    problems: Problems,
    *,
    partial_progression: bool = True,
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
        spec = _build_exercise(
            raw,
            types,
            f"{path}:{key}",
            problems,
            partial_progression=partial_progression,
        )
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
    workout's range. Only the copies that really do sync are compared: two
    entries carrying one name on different loads never reach each other, so
    what they ask for is nobody else's business.
    """
    for garmin_name in config.shared_exercises():
        by_load: dict[str, list[ExerciseSpec]] = {}
        for workout in config.workouts.values():
            for spec in workout.exercises:
                if spec.garmin_name == garmin_name:
                    by_load.setdefault(spec.load, []).append(spec)

        for load, specs in by_load.items():
            ranges = {(s.rep_low, s.rep_high, s.rep_step) for s in specs}
            if len(ranges) > 1:
                problems.add(
                    f"{path}: {garmin_name} appears in several workouts on "
                    f"{load} with different rep ranges {sorted(ranges)}; a "
                    f"synced target could fall outside one of them"
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
    _reject_moved_settings(settings, path)
    garmin_raw = settings.get("garmin") or {}
    defaults = GarminSettings()
    # A declared store is an instruction: second-guessing it would move
    # someone's credentials for them.
    declared_store = garmin_raw.get("token_store")
    declared_dumps = garmin_raw.get("dump_dir")
    garmin = GarminSettings(
        token_store=(
            os.path.expanduser(declared_store)
            if declared_store
            else default_token_store()
        ),
        activity_search_limit=int(
            garmin_raw.get("activity_search_limit") or defaults.activity_search_limit
        ),
        dump_dir=(
            os.path.expanduser(declared_dumps) if declared_dumps else default_dump_dir()
        ),
        activity_caching=_flag(
            garmin_raw.get("activity_caching"),
            "garmin.activity_caching",
            defaults.activity_caching,
        ),
    )
    _warn_if_wandering(garmin)

    # Whether a hit after a stall may move only some of the sets. Resolved onto
    # every exercise, the way a weight step is: the rules read it
    # off the spec in hand rather than being handed the settings.
    partial_progression = _flag(
        settings.get("partial_progression"), "partial_progression", True
    )

    # Unset means "ask Garmin", which is the better answer for anyone who
    # weighs in: it stays current on its own. Stating it here is for accounts
    # with no weigh-ins, and for tests that should not need a network.
    declared_bodyweight = settings.get("bodyweight")
    bodyweight = None if declared_bodyweight is None else float(declared_bodyweight)

    problems = Problems()
    types = _load_types(data.get("load"), path, problems)
    workouts: dict[str, Workout] = {}
    for entry in data["workouts"]:
        workout = _build_workout(
            entry, types, path, problems, partial_progression=partial_progression
        )
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

    if bodyweight is not None and bodyweight <= 0:
        problems.add(f"{path}: settings.bodyweight is {bodyweight:g}")
        bodyweight = None

    config = Config(workouts=workouts, garmin=garmin, path=path, bodyweight=bodyweight)
    _check_shared(config, path, problems)

    problems.raise_any()
    return config
