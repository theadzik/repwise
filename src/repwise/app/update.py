"""Advance targets from the sessions that were actually trained.

The command in one function, plus the pieces that make a multi-session run
behave: one payload per workout however many sessions touch it, the sessions
replayed oldest first, and a target that moves propagated into every other
workout containing that exercise.
"""

import json
import logging
import os
from collections.abc import Callable, Container, Hashable, Iterable
from dataclasses import dataclass, field, replace
from typing import Any

from ..config import ConfigError, record_workout_id
from ..domain.matching import normalise
from ..domain.models import Config, ExerciseSpec, Workout
from ..domain.progression import (
    PerformedSet,
    Session,
    Target,
    miss_streak,
    working_weight,
)
from ..errors import ActivityNotFound, ExitCode, GarminError, UsageError
from ..garmin.catalog import optional
from ..garmin.client import GarminSession
from ..garmin.payloads import new_workout, performed_sets
from ..planner import (
    History,
    Performed,
    Plan,
    decided_targets,
    executed_targets,
    find_workout,
    index_specs,
    logged_for,
    plan_sync,
    plan_workout,
)
from .fetch import cache_activities
from .report import report_plan

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UpdateOptions:
    """The flags `update` was given, checked once rather than read as strings."""

    apply: bool = False
    activity: str | None = None
    dump: bool = False
    push: bool = False

    def __post_init__(self) -> None:
        # Refused here rather than in the command, so that a combination which
        # cannot be honoured is rejected before anything talks to Garmin.
        if self.push and not self.apply:
            raise UsageError(
                "--push only makes sense with --apply: there is nothing to send yet."
            )


@dataclass(frozen=True)
class Trained:
    """A workout, the session that trained it, and the sessions before that.

    The earlier ones travel with it because they are only ever wanted for that
    workout, and finding them means the same scan that found this session.
    """

    workout: Workout
    activity: dict[str, Any]
    earlier: list[dict[str, Any]] = field(default_factory=list)

    @property
    def activity_id(self) -> str:
        return str(self.activity["activityId"])


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
    session: GarminSession,
    config: Config,
    activity_id: str | None,
    activities: list[dict[str, Any]],
) -> list[Trained]:
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
        workout = find_workout(config, activity.get("activityName") or "")
        return [
            Trained(
                workout,
                activity,
                sessions_before(activities, workout, activity_id),
            )
        ]

    # Garmin returns activities newest first, so the first match for a workout
    # is its latest session, and a lower position means more recent.
    found: list[tuple[int, Workout, dict[str, Any]]] = []
    for workout in config:
        if workout.garmin_workout_id is None:
            continue  # not in Garmin yet, so there is no definition to advance
        for position, activity in enumerate(activities):
            if workout.claims(activity.get("activityName") or ""):
                found.append((position, workout, activity))
                break

    if not found:
        prefixes = [p for w in config for p in w.activity_prefixes]
        raise ActivityNotFound(
            f"No recent activity matching {prefixes}. "
            "Pass --activity <id> to choose one explicitly."
        )

    found.sort(key=lambda each: each[0], reverse=True)
    return [
        Trained(
            workout,
            activity,
            sessions_before(activities, workout, str(activity["activityId"])),
        )
        for _, workout, activity in found
    ]


def matching_activities(
    activities: list[dict[str, Any]], workout: Workout
) -> list[dict[str, Any]]:
    """Every activity belonging to this workout, newest first."""
    return [
        activity
        for activity in activities
        if workout.claims(activity.get("activityName") or "")
    ]


def sessions_before(
    activities: list[dict[str, Any]], workout: Workout, activity_id: str
) -> list[dict[str, Any]]:
    """This workout's activities older than the one being judged, newest first.

    Found by position rather than by date: Garmin returns them newest first, so
    everything past the one in hand is older than it. An activity too old to
    appear in the search at all leaves no history, which reads as no stall -
    the same answer as a first-ever session, and the right one to give when we
    cannot see far enough back to say otherwise.
    """
    mine = matching_activities(activities, workout)
    ids = [str(activity["activityId"]) for activity in mine]
    if activity_id not in ids:
        return []
    return mine[ids.index(activity_id) + 1 :]


def wants_more(
    spec: ExerciseSpec, logged: list[PerformedSet], earlier: list[Session]
) -> bool:
    """Whether one more session back could still change this exercise's streak.

    The walk that counts a streak stops of its own accord at a session that
    hit, at a change of load, or at `sets - 1` misses. If instead it ran off
    the end of what we hold, there may be more to count and the next session
    back is worth fetching; anything else is already settled. So one activity
    answers a smoothly progressing exercise, and only a genuine stall reads
    deeper.
    """
    limit = max(spec.sets - 1, 0)
    if not logged or len(earlier) >= limit:
        # Not trained in the session being judged, so there is nothing for a
        # streak to explain -- or already as deep as the rules can read.
        return False
    if not earlier:
        return True

    return miss_streak(spec, earlier, working_weight(logged)) == len(earlier)


def gather_history(
    session: GarminSession,
    workout: Workout,
    activities: list[dict[str, Any]],
    latest: Performed,
) -> History:
    """What each exercise did before the latest session, newest first.

    Walks back one activity at a time and stops as soon as no exercise could
    still be counting, so a workout progressing smoothly costs one extra
    activity and only a stall costs more. Two requests each: the sets that were
    logged, and the workout they were performed against.

    An exercise missing from an older session -- added to the routine since, or
    trained under a different workout -- contributes nothing to its own history
    rather than breaking anyone else's.
    """
    specs = index_specs(workout.exercises)

    trained = {}
    for spec in workout.exercises:
        logged = logged_for(spec, latest, specs)
        if spec.time_based:
            logged = [entry.as_time() for entry in logged]
        trained[normalise(spec.garmin_name)] = logged

    history: History = {}
    for activity in activities:
        if not any(
            wants_more(
                spec,
                trained.get(normalise(spec.garmin_name)) or [],
                history.get(normalise(spec.garmin_name)) or [],
            )
            for spec in workout.exercises
        ):
            break

        activity_id = str(activity["activityId"])
        logger.debug(f"Reading back {activity.get('activityName')} ({activity_id})")
        targets = executed_targets(workout, session.executed_workout(activity_id))
        performed = performed_sets(session.exercise_sets(activity_id))

        for spec in workout.exercises:
            key = normalise(spec.garmin_name)
            target = targets.get(key)
            logged = logged_for(spec, performed, specs)
            if target is None or not logged:
                continue
            if spec.time_based:
                logged = [entry.as_time() for entry in logged]
            history.setdefault(key, []).append(Session(target, logged))

    return history


def garmin_id(workout: Workout) -> str:
    """The Garmin id of a workout that is known to have one.

    Every write goes through here: by the time one happens the workout has
    either been found in Garmin or just been created there. Stating that in
    one place keeps the writes free of `or ""`, and turns an impossible state
    into a message rather than a failed request.
    """
    if workout.garmin_workout_id is None:
        raise GarminError(f"{workout.key} has no Garmin workout id.")
    return workout.garmin_workout_id


def counted_as(workout: Workout) -> str:
    """What identifies a workout while counting up what a run would do.

    Its Garmin id once it has one, and its config key before that: a workout
    still to be created has to be countable too, and a dry run never gives it
    an id to be counted by.
    """
    return workout.garmin_workout_id or workout.key


def changed_steps(plans: list[Plan]) -> set[tuple[str, str]]:
    """Which steps moved, counted once however many plans moved them.

    Two sessions can both decide a shared exercise, and the second decision is
    a second Change on the same step rather than another step changing.
    """
    return {
        (counted_as(plan.workout), change.spec.garmin_name)
        for plan in plans
        for change in plan.moved
    }


def counted(plans: list[Plan], keys: Callable[[Plan], Iterable[Hashable]]) -> int:
    """Distinct (workout, key) pairs across every plan.

    The same deduplication `changed_steps` applies, for everything else a
    summary counts: a shared exercise refreshed in two workouts is two
    genuinely different steps and counts twice, while two plans touching the
    same step of one workout count it once.
    """
    return len(
        {(counted_as(plan.workout), key) for plan in plans for key in keys(plan)}
    )


def sync_other_workouts(
    payloads: Payloads,
    config: Config,
    source: Workout,
    targets: dict[str, Target],
) -> list[Plan]:
    """Propagate decided targets into every other workout that shares them."""
    plans: list[Plan] = []
    for other in config:
        if other.key == source.key or other.garmin_workout_id is None:
            continue
        if not any(normalise(s.garmin_name) in targets for s in other.exercises):
            continue

        payload = payloads[garmin_id(other)]
        plan = plan_sync(other, payload, targets, source.key)
        if not plan.writable:
            continue

        logger.info("")
        logger.info(f"Also in {other.key} (workout {other.garmin_workout_id}):")
        logger.info("")
        report_plan(plan)
        plans.append(plan)

    return plans


def definition_for(payloads: Payloads, workout: Workout) -> dict[str, Any]:
    """The Garmin definition to plan against: the stored one, or a fresh shell.

    A workout with no id has nothing stored, so it starts from an empty
    workout named after its config key and is filled in by the planner like
    any other. Nothing has been created at this point - a dry run gets the
    same shell and simply never sends it.
    """
    if workout.garmin_workout_id is None:
        return new_workout(workout.key)
    return payloads[workout.garmin_workout_id]


def shape_untrained(
    payloads: Payloads,
    config: Config,
    trained: set[str],
    *,
    trusted: Container[str] | None = None,
) -> list[Plan]:
    """Bring every workout with no session behind it in line with the config.

    Only the shape: which exercises, in what order, how many sets, resting how
    long, described how. Nothing here was earned in a session, so no target
    moves and nothing is warned about not having been performed.
    """
    plans: list[Plan] = []
    for workout in config:
        if workout.key in trained:
            continue

        plan = plan_workout(workout, definition_for(payloads, workout), trusted=trusted)
        if not plan.writable:
            continue

        logger.info("")
        if workout.garmin_workout_id is None:
            logger.info(f"Creating: {workout.key}")
        else:
            logger.info(
                f"Shaping: {workout.key} -> workout {workout.garmin_workout_id}"
            )
        logger.info("")
        report_plan(plan)
        plans.append(plan)

    return plans


def advance_trained(  # noqa: PLR0913 - each argument is one independent input
    session: GarminSession,
    payloads: Payloads,
    config: Config,
    options: UpdateOptions,
    sessions: list[Trained],
    *,
    trusted: Container[str] | None = None,
) -> tuple[list[Plan], bool]:
    """Plan every session that was trained, oldest first.

    Returns the plans, and whether any session turned out to hold working sets
    at all - an activity with none is not a run that failed, but it is not one
    that learned anything either.
    """
    plans: list[Plan] = []
    usable = False

    for position, trained in enumerate(sessions):
        workout, activity = trained.workout, trained.activity
        activity_id = trained.activity_id
        if position:
            logger.info("")
        logger.info(f"Activity: {activity.get('activityName') or ''} ({activity_id})")
        logger.info(f"Updating: {workout.key} -> workout {workout.garmin_workout_id}")
        logger.info("")

        sets_payload = session.exercise_sets(activity_id)
        payload = payloads[garmin_id(workout)]

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

        # What this session was actually asked for, which is not necessarily
        # what the workout holds now: a previous run may already have advanced
        # it, and judging the same activity against the target it earned would
        # read as a miss on every set.
        asked = executed_targets(workout, session.executed_workout(activity_id))

        # How long each exercise had been stalling, which is what decides how
        # far a session that hit moves it. Read back from the sessions before
        # this one, and only as far as one of them is still unsettled.
        history = gather_history(session, workout, trained.earlier, performed)

        plan = plan_workout(
            workout, payload, performed, history, asked, trusted=trusted
        )
        report_plan(plan)
        plans.append(plan)

        # Anything that moved must move everywhere that exercise appears.
        targets = decided_targets(plan)
        if targets:
            plans.extend(sync_other_workouts(payloads, config, workout, targets))

    return plans, usable


def push_to_watch(session: GarminSession, workouts: list[Workout]) -> None:
    """Queue each written workout for the watch to collect on its next sync.

    Editing a workout in Garmin Connect does not reach the watch on its own;
    the device only collects a new copy when a message is waiting for it. The
    message goes to the device you last used.
    """
    for workout in workouts:
        session.push_workout(garmin_id(workout))

    logger.info("")
    logger.info(f"Queued {len(workouts)} send(s) to your last-used device.")
    logger.info("Sync your watch to pick up the new targets.")
    _report_queue(session)


def _report_queue(session: GarminSession) -> None:
    """Read the queue back, which is the only way to confirm a push landed.

    Behind --verbose because it costs a request and a successful push says so
    already; it earns its place when a push seems not to have arrived, which
    is exactly when the queue is the thing to look at. Failing to read it back
    is not a reason to fail a push that already succeeded.
    """
    if not logger.isEnabledFor(logging.DEBUG):
        return
    try:
        pending = session.pending_messages()
    except GarminError as exc:
        logger.debug(f"Could not read the device queue back: {exc}")
        return
    logger.debug(f"{len(pending)} message(s) now waiting for your device(s).")


def run_update(
    session: GarminSession, config: Config, options: UpdateOptions
) -> ExitCode:
    payloads = Payloads(session)
    plans: list[Plan] = []

    # A run with no session behind it still has work to do: the config decides
    # the shape of a workout, and a config edit is not something to sit on
    # until the next time that workout is trained.
    untrained: ActivityNotFound | None = None
    # Fetched once and used twice: to find the session each workout was last
    # trained in, and then to read back the ones before it.
    activities = session.recent_activities()
    # With caching on, everything below reads sessions off disk, so the disk
    # is brought level with Garmin first. Without it, this does nothing at all.
    cache_activities(session, config, activities)
    try:
        sessions = pick_sessions(session, config, options.activity, activities)
    except ActivityNotFound as exc:
        sessions, untrained = [], exc

    # Garmin's published names, which decide whether an exercise it holds under
    # a different name is reused or rebuilt. See `_reconcile`.
    catalog = optional(
        config.garmin,
        "an exercise Garmin holds under another name may be reused rather than rebuilt",
    )
    trusted = catalog.names() if catalog else None

    plans.extend(
        shape_untrained(
            payloads, config, {each.workout.key for each in sessions}, trusted=trusted
        )
    )

    trained, usable = advance_trained(
        session, payloads, config, options, sessions, trusted=trusted
    )
    plans.extend(trained)

    if not usable and not plans:
        # Nothing was trained and nothing needed shaping, so the run has
        # genuinely found nothing to do. A missing activity is the more useful
        # thing to say when that is why.
        if untrained is not None:
            raise untrained
        return ExitCode.NOTHING_USABLE

    if untrained is not None:
        logger.info("")
        logger.info(f"{untrained} Shaping the workouts from the config regardless.")

    updated = len(changed_steps(plans))
    noted = counted(plans, lambda plan: (c.spec.garmin_name for c in plan.notes))
    rested = counted(plans, lambda plan: (c.spec.garmin_name for c in plan.rests))
    recounted = counted(plans, lambda plan: (c.spec.garmin_name for c in plan.sets))
    unskipped = counted(plans, lambda plan: (c.spec.garmin_name for c in plan.skips))
    # One per workout however many gap steps it touched: the config says it once.
    regaps = len({counted_as(plan.workout) for plan in plans if plan.gaps})
    shaped = counted(plans, lambda plan: ((c.kind, c.name) for c in plan.reshaped))
    structure = (
        f", {shaped} exercise(s) would be added, removed or moved" if shaped else ""
    )
    counts = f", {recounted} set count(s) would change" if recounted else ""
    between = (
        f", the rest between exercises would change in {regaps} workout(s)"
        if regaps
        else ""
    )
    notes = f", {noted} note(s) would be refreshed" if noted else ""
    rests = f", {rested} rest time(s) would change" if rested else ""
    skips = (
        f", {unskipped} step(s) would stop skipping their last rest"
        if unskipped
        else ""
    )

    if not options.apply:
        logger.info("")
        logger.info(
            f"Dry run: {updated} step(s) would change"
            f"{structure}{counts}{rests}{skips}{between}{notes}. Re-run with --apply."
        )
        return ExitCode.OK

    if not (updated or noted or rested or recounted or unskipped or regaps or shaped):
        logger.info("")
        logger.info("Nothing to write.")
        return ExitCode.OK

    written = _write(session, config, plans)

    logger.info("")
    logger.info(
        f"Wrote {updated} updated step(s) to Garmin."
        + (f" Added, removed or moved {shaped} exercise(s)." if shaped else "")
        + (f" Set {recounted} set count(s)." if recounted else "")
        + (f" Set {rested} rest time(s)." if rested else "")
        + (f" Restored the last rest on {unskipped} step(s)." if unskipped else "")
        + (f" Set the rest between exercises in {regaps} workout(s)." if regaps else "")
        + (f" Refreshed {noted} note(s)." if noted else "")
    )

    if options.push:
        push_to_watch(session, written)

    return ExitCode.OK


def _write(session: GarminSession, config: Config, plans: list[Plan]) -> list[Workout]:
    """Save every workout that has something to save, once each.

    A workout can appear in more than one plan - its own, plus a sync from a
    later session - but every plan mutated the same payload, so one write
    carries all of them.

    A workout Garmin does not have yet is created here instead, which is the
    only difference between the two: one workout, one request, either way.
    """
    written: list[Workout] = []
    saved: set[str] = set()
    for each in plans:
        if not each.writable:
            continue

        if each.workout.garmin_workout_id is None:
            written.append(_create(session, config, each))
            continue

        workout_id = garmin_id(each.workout)
        if workout_id in saved:
            continue
        saved.add(workout_id)
        session.save_workout(workout_id, each.payload)
        logger.info(f"Wrote {each.workout.key} (workout {workout_id})")
        written.append(each.workout)
    return written


def _create(session: GarminSession, config: Config, plan: Plan) -> Workout:
    """Add a workout to Garmin and record the id it was given.

    The id goes into workouts.yaml straight away, and the run carries on with
    it, so that everything downstream - a sync into this workout, a push to the
    watch, the next run recognising it - treats it as any other workout.

    A config that cannot be updated is worth stopping for: the workout now
    exists in Garmin and nothing in the file points at it, so the next run
    would build a second one.
    """
    workout = plan.workout
    workout_id = session.create_workout(plan.payload)
    logger.info(f"Created {workout.key} (workout {workout_id})")

    try:
        record_workout_id(config.path, workout.key, workout_id)
    except ConfigError as exc:
        raise ConfigError(
            f"{workout.key} was created in Garmin as {workout_id}, but its id "
            f"could not be written back: {exc}. Add "
            f'garmin_workout_id: "{workout_id}" to it by hand, or the next run '
            f"will create a second copy."
        ) from exc

    logger.info(f"Recorded its id in {config.path}")
    created = replace(workout, garmin_workout_id=workout_id)
    config.workouts[workout.key] = created
    return created
