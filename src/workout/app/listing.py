"""Show the Garmin workouts in the account, with the ids workouts.yaml needs."""

from __future__ import annotations

import logging

from ..domain.models import Config
from ..garmin.client import STRENGTH, GarminSession
from .errors import EXIT_NOTHING_USABLE, EXIT_OK

logger = logging.getLogger(__name__)


def run_list(session: GarminSession, config: Config, every_sport: bool = False) -> int:
    workouts = session.list_workouts(sport_type=None if every_sport else STRENGTH)

    if not workouts:
        logger.warning("No workouts found.")
        return EXIT_NOTHING_USABLE

    known = {w.garmin_workout_id for w in config}
    logger.info(f"{'ID':<12} {'UPDATED':<11} {'':<3}NAME")
    for entry in workouts:
        workout_id = str(entry.get("workoutId"))
        updated = (entry.get("updateDate") or "")[:10]
        mark = "*" if workout_id in known else " "
        name = entry.get("workoutName") or "(unnamed)"
        if every_sport:
            kind = (entry.get("sportType") or {}).get("sportTypeKey", "?")
            name = f"{name}  [{kind}]"
        logger.info(f"{workout_id:<12} {updated:<11} {mark:<3}{name}")

    logger.info("")
    logger.info(f"{len(workouts)} workout(s); * already in your config")
    return EXIT_OK
