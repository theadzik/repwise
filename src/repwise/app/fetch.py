"""Download what Garmin holds as JSON: your workouts, or its exercise catalog."""

import json
import logging
import os

from ..domain.models import Config, GarminSettings
from ..errors import ExitCode, GarminError
from ..garmin import catalog
from ..garmin.client import GarminSession

logger = logging.getLogger(__name__)


def run_fetch_exercises(settings: GarminSettings) -> ExitCode:
    """Download Garmin's exercise catalog, replacing any cached copy.

    Unconditional, unlike the first-run download `check` does for itself:
    asking for the catalog by name is how you refresh one that has gone stale,
    so finding a copy already there is not a reason to stop.

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

        path = os.path.join(config.garmin.dump_dir, f"workout-{workout_id}.json")
        with open(path, "w") as fh:
            json.dump(payload, fh, indent=2)
        logger.info(f"Saved {payload.get('workoutName', '(unnamed)')} -> {path}")

    return ExitCode.NOTHING_USABLE if failed else ExitCode.OK
