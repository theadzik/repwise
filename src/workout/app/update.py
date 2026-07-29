"""Advance targets from the sessions that were actually trained.

The command in one function, plus the pieces that make a multi-session run
behave: one payload per workout however many sessions touch it, the sessions
replayed oldest first, and a target that moves propagated into every other
workout containing that exercise.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from ..domain.matching import normalise
from ..domain.models import Config, Workout
from ..domain.progression import Target
from ..garmin.client import GarminSession
from ..garmin.payloads import performed_sets
from ..planner import (
    ActivityNotFound,
    Plan,
    decided_targets,
    find_workout,
    plan_sync,
    plan_workout,
)
from .errors import EXIT_CONFIG, EXIT_NOTHING_USABLE, EXIT_OK
from .report import report_plan

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UpdateOptions:
    """The flags `update` was given, checked once rather than read as strings."""

    apply: bool = False
    activity: str | None = None
    dump: bool = False
    push: bool = False


class Payloads:
    """Every workout definition this run touches, fetched at most once.

    A workout can be planned from its own session and then have a target
    synced into it from a later one, and both mutate the payload in place. Two
    separately fetched copies would mean the second write silently undid the
    first, so everything shares one dict per workout.
    """

    def __init__(self, session: GarminSession) -> None:
        self._session = session
        self._fetched: dict[str, dict[str, Any]] = {}

    def __getitem__(self, workout_id: str) -> dict[str, Any]:
        if workout_id not in self._fetched:
            self._fetched[workout_id] = self._session.workout(workout_id)
        return self._fetched[workout_id]


def dump(payloads: dict[str, Any], directory: str, suffix: str) -> None:
    for label, payload in payloads.items():
        path = os.path.join(directory, f"dump-{label}-{suffix}.json")
        with open(path, "w") as fh:
            json.dump(payload, fh, indent=2)
        logger.debug(f"Wrote {path}")


def pick_sessions(
    session: GarminSession, config: Config, activity_id: str | None
) -> list[tuple[Workout, dict[str, Any]]]:
    """The sessions to learn from, oldest first.

    With --activity, only the one named. Otherwise the latest activity for
    every workout, so training A and then B and running once advances both
    rather than only whichever came last.

    Oldest first is what makes the result independent of how long you leave
    between runs: the sessions are replayed in the order they happened, which
    is exactly what running the tool after each of them would have done. It
    also settles a shared exercise in favour of the most recent session, which
    should have the last word on it.
    """
    if activity_id:
        activity = session.activity(activity_id)
        return [(find_workout(config, activity.get("activityName") or ""), activity)]

    # Garmin returns activities newest first, so the first match for a workout
    # is its latest session, and a lower position means more recent.
    activities = session.recent_activities()
    found: list[tuple[int, Workout, dict[str, Any]]] = []
    for workout in config:
        for position, activity in enumerate(activities):
            name = (activity.get("activityName") or "").lower()
            if any(name.startswith(prefix) for prefix in workout.activity_prefixes):
                found.append((position, workout, activity))
                break

    if not found:
        prefixes = [p for w in config for p in w.activity_prefixes]
        raise ActivityNotFound(
            f"No recent activity matching {prefixes}. "
            "Pass --activity <id> to choose one explicitly."
        )

    found.sort(key=lambda each: each[0], reverse=True)
    return [(workout, activity) for _, workout, activity in found]


def changed_steps(plans: list[Plan]) -> set[tuple[str, str]]:
    """Which steps moved, counted once however many plans moved them.

    Two sessions can both decide a shared exercise, and the second decision is
    a second Change on the same step rather than another step changing.
    """
    return {
        (plan.workout.garmin_workout_id, change.spec.garmin_name)
        for plan in plans
        for change in plan.moved
    }


def noted_steps(plans: list[Plan]) -> set[tuple[str, str]]:
    """Which steps had their notes rewritten, counted once per step.

    A shared exercise is refreshed in each workout it appears in, and those
    are genuinely different steps, so they count separately.
    """
    return {
        (plan.workout.garmin_workout_id, name) for plan in plans for name in plan.notes
    }


def sync_other_workouts(
    payloads: Payloads,
    config: Config,
    source: Workout,
    targets: dict[str, Target],
) -> list[Plan]:
    """Propagate decided targets into every other workout that shares them."""
    plans: list[Plan] = []
    for other in config:
        if other.key == source.key:
            continue
        if not any(normalise(s.garmin_name) in targets for s in other.exercises):
            continue

        payload = payloads[other.garmin_workout_id]
        plan = plan_sync(other, payload, targets, source.key)
        if not plan.writable:
            continue

        logger.info("")
        logger.info(f"Also in {other.key} (workout {other.garmin_workout_id}):")
        report_plan(plan, force_flag="*")
        plans.append(plan)

    return plans


def push_to_watch(session: GarminSession, workouts: list[Workout]) -> None:
    """Queue each written workout for the watch to collect on its next sync.

    Editing a workout in Garmin Connect does not reach the watch on its own;
    the device only collects a new copy when a message is waiting for it. The
    message goes to the device you last used.
    """
    for workout in workouts:
        session.push_workout(workout.garmin_workout_id)

    logger.info("")
    logger.info(f"Queued {len(workouts)} send(s) to your last-used device.")
    logger.info("Sync your watch to pick up the new targets.")


def run_update(session: GarminSession, config: Config, options: UpdateOptions) -> int:
    if options.push and not options.apply:
        logger.error(
            "--push only makes sense with --apply: there is nothing to send yet."
        )
        return EXIT_CONFIG

    payloads = Payloads(session)
    sessions = pick_sessions(session, config, options.activity)

    plans: list[Plan] = []
    usable = False

    for position, (workout, activity) in enumerate(sessions):
        activity_id = str(activity["activityId"])
        if position:
            logger.info("")
        logger.info(f"Activity: {activity.get('activityName') or ''} ({activity_id})")
        logger.info(f"Updating: {workout.key} -> workout {workout.garmin_workout_id}")
        logger.info("")

        sets_payload = session.exercise_sets(activity_id)
        payload = payloads[workout.garmin_workout_id]

        if options.dump:
            dump(
                {"sets": sets_payload, "workout": payload},
                config.garmin.dump_dir,
                activity_id,
            )

        performed = performed_sets(sets_payload)
        if not any(performed):
            logger.warning("No working sets found in that activity; nothing to do.")
            continue
        usable = True

        plan = plan_workout(workout, payload, performed)
        report_plan(plan)
        plans.append(plan)

        # Anything that moved must move everywhere that exercise appears.
        targets = decided_targets(plan)
        if targets:
            plans.extend(sync_other_workouts(payloads, config, workout, targets))

    if not usable:
        return EXIT_NOTHING_USABLE

    updated = len(changed_steps(plans))
    noted = len(noted_steps(plans))
    notes = f", {noted} note(s) would be refreshed" if noted else ""

    if not options.apply:
        logger.info("")
        logger.info(
            f"Dry run: {updated} step(s) would change{notes}. Re-run with --apply."
        )
        return EXIT_OK

    if not updated and not noted:
        logger.info("")
        logger.info("Nothing to write.")
        return EXIT_OK

    written = _write(session, plans)

    logger.info("")
    logger.info(
        f"Wrote {updated} updated step(s) to Garmin."
        + (f" Refreshed {noted} note(s)." if noted else "")
    )

    if options.push:
        push_to_watch(session, written)

    return EXIT_OK


def _write(session: GarminSession, plans: list[Plan]) -> list[Workout]:
    """Save every workout that has something to save, once each.

    A workout can appear in more than one plan - its own, plus a sync from a
    later session - but every plan mutated the same payload, so one write
    carries all of them.
    """
    written: list[Workout] = []
    saved: set[str] = set()
    for each in plans:
        if not each.writable or each.workout.garmin_workout_id in saved:
            continue
        saved.add(each.workout.garmin_workout_id)
        session.save_workout(each.workout.garmin_workout_id, each.payload)
        logger.info(
            f"Wrote {each.workout.key} (workout {each.workout.garmin_workout_id})"
        )
        written.append(each.workout)
    return written
