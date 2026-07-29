"""Download workout definitions as JSON, for inspection or a connectivity check."""

from __future__ import annotations

import json
import logging
import os

from ..domain.models import Config
from ..garmin.client import GarminSession
from .errors import EXIT_NOTHING_USABLE, EXIT_OK

logger = logging.getLogger(__name__)


def run_fetch(
    session: GarminSession, config: Config, workout_ids: list[str] | None = None
) -> int:
    ids = workout_ids or [w.garmin_workout_id for w in config]
    failed = False
    for workout_id in ids:
        try:
            payload = session.workout(workout_id)
        except Exception as exc:  # noqa: BLE001 - report and carry on
            logger.error(f"FAILED {workout_id}: {exc}")
            failed = True
            continue

        path = os.path.join(config.garmin.dump_dir, f"workout-{workout_id}.json")
        with open(path, "w") as fh:
            json.dump(payload, fh, indent=2)
        logger.info(f"Saved {payload.get('workoutName', '(unnamed)')} -> {path}")

    return EXIT_NOTHING_USABLE if failed else EXIT_OK
