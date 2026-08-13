"""The dump directory: what is in it, and when a copy may be believed.

Two jobs, and the second is why the first is here rather than in whichever
module happened to want it. Every Garmin payload this tool saves is named the
same way - `<kind>-<id>.json`, in `settings.garmin.dump_dir` - and `fetch`
writes them so that they can be read by hand. With
`settings.garmin.activity_caching` on, the same files are also read back
*instead of* asking Garmin again, which turns a directory of dumps into a
cache and raises the only hard question here: when is a copy still true?

A performed session is finished, so most of it never changes. What does change
is what the watch got wrong and you corrected in Connect afterwards - a rep it
missed, a set it invented. Garmin reports `totalSets`, `totalReps` and
`totalVolume` for every strength activity in the list of recent ones, which
`update` fetches anyway, so an edit is detectable without spending a request on
it. `ActivityCache` records those three numbers for each session it files, and
forgets the session as soon as Garmin's copy of them stops matching. The index
holding them is the record of what has been filed, which is also what lets an
absent `executed-<id>.json` mean "asked, and there is no workout behind this
one" rather than "never asked".

Nothing here talks to Garmin. `garmin/client.py` is what puts a session in
front of this, and `app/fetch.py` is what fills it.
"""

import json
import logging
import os
from typing import Any

from .errors import UsageError

logger = logging.getLogger(__name__)

#: The payloads a performed session is kept as, one file each. An `EXECUTED`
#: holding `[]` is a session performed against no workout, written rather than
#: left out so that a missing file means one thing only: ask Garmin. Deleting
#: one by hand is therefore always a way to have it downloaded again.
ACTIVITY = "activity"
SETS = "sets"
EXECUTED = "executed"
SESSION = (ACTIVITY, SETS, EXECUTED)

#: A workout definition, which is not part of a session and is never cached:
#: `update` rewrites it, so the copy on disk is a snapshot of a moving thing.
WORKOUT = "workout"

#: What has been filed, and what Garmin has to still agree with for the filing
#: to be believed. Named to fall under the `activity-*.json` patterns that
#: .gitignore and the `forbid-private-files` hook already carry, because it is
#: as much your Garmin data as the payloads beside it.
INDEX_FILE = "activity-index.json"
TOTALS = ("totalSets", "totalReps", "totalVolume")


def path(directory: str, kind: str, name: str) -> str:
    """Where a payload of this kind and name belongs.

    The name is an id that came from the command line or from the config, so it
    is checked rather than trusted. Writing inside `directory` is the whole of
    what this promises, and a separator in an id would break that quietly - an
    absolute one would drop the directory altogether, since that is what
    `os.path.join` does with an absolute second half.
    """
    filename = f"{kind}-{name}.json"
    if filename != os.path.basename(filename):
        raise UsageError(
            f"Refusing to write `{filename}`: a Garmin id is a number, and one "
            f"carrying a path would land outside {directory}."
        )
    return os.path.join(directory, filename)


def write(payload: Any, directory: str, kind: str, name: str) -> str:
    """Save one payload, and say where it went."""
    destination = path(directory, kind, name)
    with open(destination, "w") as fh:
        json.dump(payload, fh, indent=2)
    return destination


def read(directory: str, kind: str, name: str) -> Any | None:
    """One saved payload, or None if it was never saved or cannot be read."""
    return _load(path(directory, kind, name))


def _load(source: str) -> Any | None:
    """The contents of one saved file, or None.

    A file that is missing and a file that is corrupt answer the same, because
    the caller does the same thing about both: ask Garmin. A half-written dump
    left by an interrupted run should cost a request, not a run.
    """
    try:
        with open(source) as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug(f"Ignoring {source}: {exc}")
        return None


def _moved(before: dict[str, Any], after: dict[str, Any]) -> str:
    """Which of Garmin's totals changed, for a line saying why a copy went."""
    if not before:
        return "it was filed before Garmin had said what it held"
    changed = [
        f"{key} {before.get(key)} -> {after.get(key)}"
        for key in TOTALS
        if before.get(key) != after.get(key)
    ]
    return ", ".join(changed) if changed else "the totals it was filed under are gone"


def totals(activity: dict[str, Any]) -> dict[str, Any]:
    """Garmin's own count of what a session held, from a list of activities.

    Only the three that move when a set is edited. Present on strength
    activities and null on the rest, which is harmless: what matters is that
    the same activity answers the same way twice.
    """
    return {key: activity.get(key) for key in TOTALS}


class ActivityCache:
    """The sessions in a directory, and whether Garmin still agrees with them.

    Reads answer from disk when the session has been filed and its files are
    still there; deleting one by hand is therefore a way to force a re-download
    of it, and is the documented one.
    """

    def __init__(self, directory: str) -> None:
        self._directory = directory
        self._index_path = os.path.join(directory, INDEX_FILE)
        self._index: dict[str, dict[str, Any]] = _load(self._index_path) or {}
        #: Totals from the last list of activities seen, so that a session
        #: filed during this run is filed with Garmin's word on it.
        self._seen: dict[str, dict[str, Any]] = {}

    @property
    def directory(self) -> str:
        return self._directory

    def reconcile(self, activities: list[dict[str, Any]]) -> None:
        """Take Garmin's word on these sessions, and drop what disagrees.

        Called with the list `update` fetches anyway, so noticing that a
        session was edited in Connect costs nothing. A session absent from the
        list is left alone: it is not being contradicted, only not mentioned.
        """
        dropped = []
        for activity in activities:
            activity_id = str(activity.get("activityId") or "")
            if not activity_id:
                continue

            current = totals(activity)
            self._seen[activity_id] = current
            filed = self._index.get(activity_id)
            if filed is not None and filed.get("totals") != current:
                logger.debug(
                    f"Cache stale for {activity_id}: "
                    f"{_moved(filed.get('totals') or {}, current)}"
                )
                del self._index[activity_id]
                dropped.append(activity_id)

        if dropped:
            logger.info(
                f"{len(dropped)} cached session(s) no longer match Garmin and "
                "will be downloaded again."
            )
            self._save()

    def _missing(self, kind: str, activity_id: str) -> str | None:
        """Why this payload cannot be answered from disk, or None if it can.

        A reason rather than a flag, because every one of them is a different
        thing to have done - never asked for, asked for but not that payload,
        deleted since - and under `--verbose` that is the difference between a
        cache working and a cache that quietly never hits.
        """
        filed = self._index.get(str(activity_id)) or {}
        if not filed:
            return "no such session has been filed"
        if kind not in (filed.get("filed") or []):
            return "that payload has never been asked for"
        if not os.path.exists(path(self._directory, kind, str(activity_id))):
            return "the file has been deleted since"
        return None

    def has(self, kind: str, activity_id: str) -> bool:
        """Whether one payload of this session can be answered from disk.

        Filed *and* still on disk, tracked per payload rather than per session
        because the three are asked for separately and in no fixed order:
        `update` reads the sets and the executed workout and never the summary,
        so a session is not all-or-nothing until something has asked for all
        three.

        Silent: this is asked to report a skip and to decide what to download,
        neither of which is a use of the cache. `load` is what logs.
        """
        return self._missing(kind, activity_id) is None

    def holds(self, activity_id: str) -> bool:
        """Whether the whole session is on disk, which is what `fetch` skips."""
        return all(self.has(kind, activity_id) for kind in SESSION)

    def load(self, kind: str, activity_id: str) -> Any | None:
        """A filed payload, or None to go and ask Garmin for it."""
        name = f"{kind}-{activity_id}.json"
        missing = self._missing(kind, activity_id)
        if missing:
            logger.debug(f"Cache miss for {name}: {missing}")
            return None

        payload = read(self._directory, kind, str(activity_id))
        if payload is None:
            # `read` has already said what was wrong with the file itself.
            logger.debug(f"Cache miss for {name}: it could not be read")
            return None

        logger.debug(f"Cache hit for {name}")
        return payload

    def store(self, kind: str, activity_id: str, payload: Any) -> None:
        """File one payload, and note that this session now holds it."""
        activity_id = str(activity_id)
        logger.debug(f"Filing {kind}-{activity_id}.json")
        write(payload, self._directory, kind, activity_id)

        # Filed against the totals Garmin reported this run. A session nobody
        # listed - an id named on the command line, or one older than the
        # search limit - has none, and is then believed until it is listed and
        # contradicted.
        entry = self._index.setdefault(
            activity_id, {"totals": self._seen.get(activity_id, {}), "filed": []}
        )
        if kind not in entry["filed"]:
            entry["filed"].append(kind)
        self._save()

    def _save(self) -> None:
        with open(self._index_path, "w") as fh:
            json.dump(self._index, fh, indent=2)
