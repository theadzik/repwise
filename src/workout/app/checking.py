"""Report where workouts.yaml and the Garmin workouts disagree."""

import logging

from ..checker import Finding, check_programming, check_workout
from ..domain.models import Config
from ..errors import ExitCode, GarminError
from ..garmin.client import GarminSession
from .report import SEVERITY

logger = logging.getLogger(__name__)


def _bodyweight(session: GarminSession, config: Config) -> float | None:
    """How much of you the bodyweight-loaded exercises are carrying.

    What the config states wins, since someone who wrote it down means it.
    Otherwise Garmin is asked, which is the answer that stays current without
    anyone editing a file. A failure here is not worth failing the command
    over: it costs the range checks on a few exercises, and `check_programming`
    reports each of those where it finds them.
    """
    if config.bodyweight is not None:
        return config.bodyweight

    if not any(
        spec.bodyweight_factor for workout in config for spec in workout.exercises
    ):
        return None  # nothing would read it, so do not spend a request on it

    try:
        weight = session.bodyweight()
    except GarminError as exc:
        logger.debug(f"Could not read your weigh-ins: {exc}")
        return None

    if weight is not None:
        logger.debug(f"Bodyweight {weight:g} kg, averaged from your Garmin weigh-ins.")
    return weight


def run_check(session: GarminSession, config: Config) -> ExitCode:
    findings: list[Finding] = []
    bodyweight = _bodyweight(session, config)
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
        found += check_programming(workout, payload, bodyweight)
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
