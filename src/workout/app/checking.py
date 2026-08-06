"""Report where workouts.yaml and the Garmin workouts disagree."""

from __future__ import annotations

import logging

from ..checker import Finding, check_workout
from ..domain.models import Config
from ..errors import ExitCode, GarminError
from ..garmin.client import GarminSession
from .report import SEVERITY

logger = logging.getLogger(__name__)


def run_check(session: GarminSession, config: Config) -> ExitCode:
    findings: list[Finding] = []
    for workout in config:
        workout_id = workout.garmin_workout_id
        if workout_id is None:
            # Nothing in Garmin to disagree with yet. Said out loud, because
            # silence here would read as "checked, and fine".
            logger.info(f"{workout.key} (not in Garmin yet)")
            logger.info("")
            continue

        try:
            payload = session.workout(workout_id)
        except GarminError as exc:
            detail = f"could not fetch workout {workout_id}: {exc}"
            logger.error(f"{workout.key}: {detail}")
            # A workout that cannot be read is itself an error-level finding,
            # so an unreachable workout still fails the command.
            findings.append(Finding(workout.key, detail, "error"))
            continue

        found = check_workout(workout, payload)
        logger.info(f"{workout.key} ({workout_id})")
        if not found:
            logger.info("  ok")
        for finding in found:
            marker, level = SEVERITY[finding.severity]
            logger.log(level, f"  {marker} {finding.detail}")
        logger.info("")
        findings.extend(found)

    # Everything reported here needs a hand, so any finding at all fails the
    # command. That is what makes it worth putting in a cron job: it goes off
    # when the config is wrong, not when you have edited a rest and not yet
    # run `update`.
    logger.info(f"{len(findings)} issue(s) across {len(config.workouts)} workout(s)")
    return ExitCode.NOTHING_USABLE if findings else ExitCode.OK
