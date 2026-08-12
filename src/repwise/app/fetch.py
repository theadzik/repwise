"""Download what Garmin holds as JSON: your workouts, your sessions, or its
exercise catalog."""

import json
import logging
import os
from typing import Any

from ..domain.models import Config, GarminSettings
from ..errors import ExitCode, GarminError
from ..garmin import catalog
from ..garmin.client import STRENGTH, GarminSession
from ..garmin.payloads import activity_sport

logger = logging.getLogger(__name__)


def save(payload: Any, directory: str, name: str) -> str:
    """Write one payload as `name`.json into `directory`, and say where."""
    path = os.path.join(directory, f"{name}.json")
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=2)
    return path


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

        path = save(payload, config.garmin.dump_dir, f"workout-{workout_id}")
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

    failed = False
    for activity_id in ids:
        try:
            name, paths = _save_activity(session, config.garmin.dump_dir, activity_id)
        except GarminError as exc:
            # One unreachable session should not cost the user the others.
            logger.error(f"FAILED {activity_id}: {exc}")
            failed = True
            continue

        written = ", ".join(os.path.basename(path) for path in paths)
        logger.info(f"Saved {name} -> {written}")

    logger.info("")
    logger.info(f"{len(ids)} session(s) -> {config.garmin.dump_dir}")
    return ExitCode.NOTHING_USABLE if failed else ExitCode.OK


def _save_activity(
    session: GarminSession, directory: str, activity_id: str
) -> tuple[str, list[str]]:
    """Download one session's payloads. Its name, and the files written.

    The summary is fetched even for an activity a scan just listed, so that
    `activity-<id>.json` holds the same thing however it was asked for: the
    detail Garmin returns for one activity is fuller than the entry it returns
    for it in a list.
    """
    activity = session.activity(activity_id)
    paths = [
        save(activity, directory, f"activity-{activity_id}"),
        save(session.exercise_sets(activity_id), directory, f"sets-{activity_id}"),
    ]

    executed = session.executed_workout(activity_id)
    if executed:
        paths.append(save(executed, directory, f"executed-{activity_id}"))
    else:
        logger.debug(f"{activity_id} was not performed against a workout")

    return activity.get("activityName") or "(unnamed)", paths
