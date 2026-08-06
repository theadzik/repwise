"""Download workout definitions as JSON, for inspection or a connectivity check."""

from __future__ import annotations

import json
import logging
import os

from ..domain.models import Config
from ..errors import ExitCode, GarminError
from ..garmin.client import GarminSession

logger = logging.getLogger(__name__)


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
