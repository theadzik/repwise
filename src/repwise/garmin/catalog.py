"""Garmin's exercise catalog: what exists, and what it is filed under.

Garmin publishes the list its own workout editor is built from as a static
file, with no account and no token involved: every category, and the exercises
in it. That makes it the one authority on whether a `garmin_name` in
workouts.yaml names something real - a question `check` could otherwise only
ask of exercises a Garmin workout already holds, which is too late to be much
help when the answer is no.

Cached under the token store, because it changes about as often as Garmin adds
an exercise and re-downloading it every run would be rude. The payload is
stored whole rather than reduced to the two fields read here, so that the
muscle groups in it are already on disk for whatever wants them next.
"""

import json
import logging
import os
import urllib.request
from dataclasses import dataclass
from difflib import get_close_matches

from .. import __version__
from ..domain.models import GarminSettings
from ..errors import GarminError

__all__ = [
    "ExerciseCatalog",
    "cache_path",
    "download",
    "ensure",
    "load",
    "optional",
    "save",
]

logger = logging.getLogger(__name__)

CATALOG_URL = "https://connect.garmin.com/web-data/exercises/Exercises.json"

#: Sits beside the OAuth tokens: both are per-user, disposable, and none of the
#: user's business to edit.
CACHE_NAME = "exercises.json"

#: Garmin answers 403 to urllib's default `Python-urllib/x.y`, so the client
#: names itself. This is not pretending to be a browser - the honest string is
#: served happily, and the default is refused for being the default.
USER_AGENT = f"repwise/{__version__} (+https://github.com/theadzik/repwise)"

TIMEOUT = 30


@dataclass(frozen=True)
class ExerciseCatalog:
    """Every exercise Garmin knows, by the category it files it under.

    Both halves of the pair matter, and separately: Garmin validates the
    category and the name against each other, so a real exercise under the
    wrong category is as unusable as one that does not exist.
    """

    categories: dict[str, frozenset[str]]

    @classmethod
    def parse(cls, payload: dict) -> ExerciseCatalog:
        raw = payload.get("categories")
        if not isinstance(raw, dict) or not raw:
            # Refused rather than accepted as an empty catalog, which would
            # report every exercise in the config as unknown.
            raise GarminError("The exercise catalog has no categories in it.")
        return cls(
            {
                category: frozenset(entry.get("exercises") or {})
                for category, entry in raw.items()
                if isinstance(entry, dict)
            }
        )

    def __len__(self) -> int:
        """How many exercises there are, across every category."""
        return sum(len(names) for names in self.categories.values())

    def has_category(self, category: str) -> bool:
        return category in self.categories

    def holds(self, category: str, name: str) -> bool:
        return name in self.categories.get(category, frozenset())

    def locate(self, name: str) -> list[tuple[str, str]]:
        """Where this exercise lives: every (category, name) holding it.

        Matched ignoring case, and the catalog's own spelling is returned
        rather than the one asked for, because both halves are what the caller
        is missing: someone who wrote the name in lower case needs to be told
        how Garmin spells it, not that it does not exist.
        """
        wanted = name.casefold()
        return sorted(
            (category, held)
            for category, names in self.categories.items()
            for held in names
            if held.casefold() == wanted
        )

    def names(self) -> frozenset[str]:
        """Every exercise Garmin defines, category forgotten.

        What matching needs to tell a real exercise name from one Garmin never
        published: the first is worth taking at its word, the second is what
        the category fallback is for.
        """
        return frozenset(held for names in self.categories.values() for held in names)

    def like(self, name: str, limit: int = 3) -> list[str]:
        """The nearest catalog names to one that is not in it at all.

        A high cutoff on purpose. The point is to catch a typo or a near-miss
        constant, and a loose match on 1500 names offers three exercises that
        have nothing to do with the one meant.
        """
        return get_close_matches(name, self.names(), n=limit, cutoff=0.8)


def optional(settings: GarminSettings, cost: str) -> ExerciseCatalog | None:
    """The catalog, fetched if this is the first run that wants it, or None.

    Downloaded here rather than demanded of the user, because a command that
    only works after another command has been run is a command that goes unrun,
    and because what the catalog buys `update` is correctness rather than
    convenience: without it an exercise Garmin holds under another name is
    reused instead of rebuilt. Making that protection conditional on having run
    a setup command leaves it off for exactly the people who have not. Both
    callers already log in and fetch workouts, so one public, unauthenticated,
    cached-forever request is no dependency they did not already have.

    A failure costs `cost` and nothing else, and says which command retries it
    on its own. Both callers are worth running with no network at all.
    """
    try:
        return ensure(settings)
    except GarminError as exc:
        logger.warning(f"{exc} - so {cost}.")
        logger.warning("Run `repwise fetch exercises` to try it on its own.")
        return None


def cache_path(settings: GarminSettings) -> str:
    """Where the cached catalog sits, beside the tokens.

    Expanded here rather than trusted to have been: a config file's token_store
    is expanded as it is read, but the default on `GarminSettings` is the
    literal `~/.config/repwise`, and joining that raw makes a directory called
    `~` wherever the process happens to be standing.
    """
    return os.path.join(os.path.expanduser(settings.token_store), CACHE_NAME)


def download() -> dict:
    """Fetch the catalog from Garmin.

    Deliberately not a `GarminSession` method: the file is public, and going
    through the session would make `repwise fetch exercises` prompt for a
    password on a first run to download something that needs no account.
    """
    request = urllib.request.Request(CATALOG_URL, headers={"User-Agent": USER_AGENT})
    logger.debug(f"Downloading {CATALOG_URL}")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            payload = json.load(response)
    # URLError and HTTPError are both OSError; JSONDecodeError is a ValueError.
    except (OSError, ValueError) as exc:
        raise GarminError(f"Could not download the exercise catalog: {exc}") from exc

    if not isinstance(payload, dict):
        raise GarminError("The exercise catalog was not the object we expected.")
    return payload


def save(settings: GarminSettings, payload: dict) -> str:
    """Write the catalog to the cache, and say where it went.

    The token store is created if this runs before the first login, so that
    downloading the catalog does not depend on having logged in.
    """
    path = cache_path(settings)
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as fh:
            json.dump(payload, fh)
    except OSError as exc:
        raise GarminError(f"Could not write {path}: {exc}") from exc
    return path


def load(settings: GarminSettings) -> ExerciseCatalog | None:
    """The cached catalog, or None when there is no usable one.

    A cache that cannot be read or parsed reads as absent rather than as a
    failure. It is a disposable copy of a public file, so the repair for a
    truncated one is to fetch it again - which is what `ensure` then does,
    without anyone having to know the cache was corrupt.
    """
    path = cache_path(settings)
    try:
        with open(path) as fh:
            return ExerciseCatalog.parse(json.load(fh))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, GarminError) as exc:
        logger.debug(f"Ignoring the cached catalog at {path}: {exc}")
        return None


def ensure(settings: GarminSettings) -> ExerciseCatalog:
    """The catalog, downloading and caching it if this is the first time.

    So that `check` and `update` work on a fresh checkout without a fetch first.
    Refreshing a stale copy stays explicit - `repwise fetch exercises` - because
    there is no way to tell a stale catalog from a current one without
    downloading it, and doing that every run is the cost this cache exists to
    avoid.
    """
    cached = load(settings)
    if cached is not None:
        return cached

    logger.info("No exercise catalog cached yet, downloading it.")
    payload = download()
    # Parsed before it is written, so a malformed download is not cached.
    catalog = ExerciseCatalog.parse(payload)
    logger.info(f"Cached {len(catalog)} exercises in {save(settings, payload)}")
    return catalog
