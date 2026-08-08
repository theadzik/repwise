"""Report where workouts.yaml and the Garmin workouts disagree."""

import logging

from ..checker import Finding, check_catalog, check_programming, check_workout
from ..domain.models import Config, GarminSettings
from ..errors import ExitCode, GarminError
from ..garmin.catalog import ExerciseCatalog, ensure
from ..garmin.client import GarminSession
from .report import SEVERITY

logger = logging.getLogger(__name__)


def _catalog(settings: GarminSettings) -> ExerciseCatalog | None:
    """Garmin's exercise list, downloaded on the first run that wants it.

    Fetched here rather than demanded of the user, because a check that only
    works after another command has been run is a check that goes unrun. The
    copy is cached, so this costs one download ever.

    A failure costs the name checks and nothing else, exactly as a missing
    weigh-in costs the range checks. `check` is worth running with no network
    at all, and the questions it can still answer are worth answering.
    """
    try:
        return ensure(settings)
    except GarminError as exc:
        logger.warning(f"Exercise names were not checked: {exc}")
        return None


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
    catalog = _catalog(config.garmin)
    for workout in config:
        workout_id = workout.garmin_workout_id
        # First, and outside the branch below, because it is the only check
        # that does not need Garmin to hold the workout. Reported first too: an
        # exercise that does not exist explains whatever the checks below go on
        # to say about it, which reads better before them than after.
        found = check_catalog(workout, catalog) if catalog else []

        if workout_id is None:
            # Nothing in Garmin to disagree with yet. Said out loud, because
            # silence here would read as "checked, and fine" - and the names
            # above were checked, which is the point of doing it now.
            logger.info(f"{workout.key} (not in Garmin yet)")
        else:
            logger.info(f"{workout.key} ({workout_id})")
            try:
                payload = session.workout(workout_id)
            except GarminError as exc:
                # A workout that cannot be read is itself an error-level
                # finding, so an unreachable workout still fails the command.
                found.append(
                    Finding(
                        workout.key,
                        f"could not fetch workout {workout_id}: {exc}",
                        "error",
                    )
                )
            else:
                found += check_workout(workout, payload)
                found += check_programming(workout, payload, bodyweight)

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
