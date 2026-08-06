"""Turn workouts built in Garmin Connect into workouts.yaml content."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from ..errors import ActivityNotFound, ExitCode, UsageError
from ..garmin.client import STRENGTH, GarminSession
from ..importer import describe_workout, render_config
from ..yamlio import write

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ImportOptions:
    """Which workouts to import, and where the result goes."""

    name: str | None = None
    id: str | None = None
    output: str | None = None
    force: bool = False


def select(session: GarminSession, options: ImportOptions) -> list[dict[str, Any]]:
    """The workout summaries an import should cover."""
    workouts = session.list_workouts(sport_type=STRENGTH)
    if options.id:
        wanted = [w for w in workouts if str(w.get("workoutId")) == options.id]
        if not wanted:
            raise ActivityNotFound(f"No strength workout with id {options.id}.")
        return wanted
    if options.name:
        needle = options.name.lower()
        wanted = [w for w in workouts if needle in (w.get("workoutName") or "").lower()]
        if not wanted:
            names = ", ".join(repr(w.get("workoutName")) for w in workouts)
            raise ActivityNotFound(
                f"No strength workout matching {options.name!r}. Found: {names}"
            )
        return wanted
    return workouts


def run_import(session: GarminSession, options: ImportOptions) -> ExitCode:
    summaries = select(session, options)

    imported = [
        describe_workout(session.workout(str(s["workoutId"]))) for s in summaries
    ]
    text = render_config(imported)

    if not options.output:
        # Config content, not a report: written straight out so that it stays
        # redirectable and never picks up a log prefix.
        print(text)
        return ExitCode.OK

    if os.path.exists(options.output) and not options.force:
        raise UsageError(
            f"{options.output} already exists. Pass --force to overwrite it."
        )

    write(options.output, text)

    exercises = sum(len(w.exercises) for w in imported)
    logger.info(
        f"Wrote {len(imported)} workout(s), {exercises} exercises -> {options.output}"
    )
    logger.info("Check the TODO comments before using it.")
    return ExitCode.OK
