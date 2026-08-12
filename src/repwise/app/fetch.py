"""Download what Garmin holds as JSON: your workouts, your sessions, or its
exercise catalog."""

import logging
import os
from typing import Any

from .. import dumps
from ..domain.models import Config, GarminSettings
from ..errors import ExitCode, GarminError
from ..garmin import catalog
from ..garmin.client import STRENGTH, GarminSession
from ..garmin.payloads import activity_sport

logger = logging.getLogger(__name__)


def run_fetch_exercises(settings: GarminSettings) -> ExitCode:
    """Download Garmin's exercise catalog, replacing any cached copy.

    Unconditional, unlike the first-run download `check` and `update` do for
    themselves: asking for the catalog by name is how you refresh one that has
    gone stale, so finding a copy already there is not a reason to stop.

    No session is opened. The catalog is public, and requiring a login to
    download it would be a password prompt in exchange for nothing.
    """
    payload = catalog.download()
    # Parsed for the count, and to fail before overwriting a good cache with a
    # response that turned out not to be a catalog at all.
    parsed = catalog.ExerciseCatalog.parse(payload)
    path = catalog.save(settings, payload)
    logger.info(
        f"Saved {len(parsed)} exercises in {len(parsed.categories)} "
        f"categories -> {path}"
    )
    return ExitCode.OK


def run_fetch(
    session: GarminSession, config: Config, workout_ids: list[str] | None = None
) -> ExitCode:
    # A workout Garmin does not hold yet has no definition to download, so it
    # is simply not among the ids rather than a failure to report.
    ids = workout_ids or [w.garmin_workout_id for w in config if w.garmin_workout_id]
    failed = False
    for workout_id in ids:
        try:
            payload = session.workout(workout_id)
        except GarminError as exc:
            # One unreachable workout should not cost the user the others.
            logger.error(f"FAILED {workout_id}: {exc}")
            failed = True
            continue

        path = dumps.write(payload, config.garmin.dump_dir, dumps.WORKOUT, workout_id)
        logger.info(f"Saved {payload.get('workoutName', '(unnamed)')} -> {path}")

    return ExitCode.NOTHING_USABLE if failed else ExitCode.OK


def run_fetch_activities(
    session: GarminSession, config: Config, activity_ids: list[str] | None = None
) -> ExitCode:
    """Save performed sessions as JSON, three payloads to a session.

    None of the three is either of the others. The summary names and dates the
    session; the sets are what the watch recorded, rep by rep and in grams; the
    executed workout is what that session was *asked* for, and is the only
    record of it, since `update` has since rewritten the definition Garmin
    stores. An activity performed against no workout has no third file.

    Ids name sessions directly, and are downloaded as given: asking for one by
    id says more about what you want than its sport does, and an id is also the
    only way to reach a session further back than the search limit. Without
    them the recent activities are scanned and the strength ones kept - how far
    back that reaches is `settings.garmin.activity_search_limit`.
    """
    ids = list(activity_ids or [])
    if not ids:
        recent = session.recent_activities()
        ids = [
            str(activity["activityId"])
            for activity in recent
            if activity_sport(activity) == STRENGTH and activity.get("activityId")
        ]
        if not ids:
            logger.warning(
                f"No strength activities among the {len(recent)} most recent. "
                "Raise settings.garmin.activity_search_limit to look further "
                "back, or name an activity id."
            )
            return ExitCode.NOTHING_USABLE

    saved = 0
    skipped = 0
    failed = False
    for activity_id in ids:
        # Never true unless caching is on, so a run without it downloads
        # everything it is asked for exactly as it always did. `--force` says
        # so by opening a session with no cache behind it, which is why there
        # is no flag to read here.
        if session.is_cached(activity_id):
            skipped += 1
            logger.info(f"Already on disk: {activity_id}")
            continue

        try:
            name, paths = _save_activity(session, config.garmin.dump_dir, activity_id)
        except GarminError as exc:
            # One unreachable session should not cost the user the others.
            logger.error(f"FAILED {activity_id}: {exc}")
            failed = True
            continue

        saved += 1
        written = ", ".join(os.path.basename(path) for path in paths)
        logger.info(f"Saved {name} -> {written}")

    logger.info("")
    # Counted as they land rather than taken from `ids`, so that a run which
    # lost one to a failure does not claim to have saved it.
    logger.info(f"{saved} session(s) -> {config.garmin.dump_dir}")
    if skipped:
        logger.info(f"{skipped} already on disk; --force downloads them again.")
    return ExitCode.NOTHING_USABLE if failed else ExitCode.OK


def cache_activities(
    session: GarminSession, config: Config, activities: list[dict[str, Any]]
) -> None:
    """Bring the dump directory level with the sessions Garmin just listed.

    What `update` does before it works anything out, so that the run reads
    those sessions off disk and every run after it reads them without asking
    at all. Only the strength ones: the rest hold no sets to learn from.

    Silent unless caching is on. A session with no cache behind it holds
    nothing, so every activity would count as missing and this would download
    the whole search limit on every single run.

    One session that cannot be downloaded is logged and stepped over. This is
    filling a cache, not doing the work: whatever is missing afterwards is
    fetched again when something actually reads it, and fails there if it
    still cannot be had.
    """
    if not config.garmin.activity_caching:
        return

    missing = [
        str(activity["activityId"])
        for activity in activities
        if activity_sport(activity) == STRENGTH
        and activity.get("activityId")
        and not session.is_cached(str(activity["activityId"]))
    ]
    if not missing:
        logger.debug("Every strength session Garmin listed is already on disk.")
        return

    logger.info(f"Filing {len(missing)} session(s) into {config.garmin.dump_dir}")
    for activity_id in missing:
        try:
            _save_activity(session, config.garmin.dump_dir, activity_id)
        except GarminError as exc:
            logger.warning(f"Could not file {activity_id}: {exc}")


def _save_activity(
    session: GarminSession, directory: str, activity_id: str
) -> tuple[str, list[str]]:
    """Download one session's payloads. Its name, and the files written.

    The summary is fetched even for an activity a scan just listed, so that
    `activity-<id>.json` holds the same thing however it was asked for: the
    detail Garmin returns for one activity is fuller than the entry it returns
    for it in a list.

    All three are written, including an executed workout that came back empty.
    A session performed against no workout is a fact worth recording, and it is
    what lets a missing file mean one thing only - that nobody has asked yet -
    which is what `dumps.ActivityCache` reads it as.

    Behind a caching session the same bytes have just been written by the cache
    itself, since that is what a miss does. Writing them again is what makes
    this work identically with caching off, which is worth more than the write
    it saves - once per session, of a file that is already open in the page
    cache.
    """
    activity = session.activity(activity_id)
    executed = session.executed_workout(activity_id)
    if not executed:
        logger.debug(f"{activity_id} was not performed against a workout")

    paths = [
        dumps.write(activity, directory, dumps.ACTIVITY, activity_id),
        dumps.write(
            session.exercise_sets(activity_id), directory, dumps.SETS, activity_id
        ),
        dumps.write(executed, directory, dumps.EXECUTED, activity_id),
    ]

    return activity.get("activityName") or "(unnamed)", paths
