"""Command line interface: argument parsing and output formatting.

The user-facing help text lives in `build_parser()` rather than in this
docstring, so that it can be laid out deliberately.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any

from .checker import Finding, check_workout
from .config import DEFAULT_CONFIG, ConfigError, load_config
from .garmin.client import (
    STRENGTH,
    GarminConnectTooManyRequestsError,
    GarminSession,
    connect,
)
from .garmin.payloads import performed_sets
from .importer import describe_workout, render_config
from .log import configure
from .models import Config, ExerciseSpec, Workout
from .planner import (
    ActivityNotFound,
    Change,
    Plan,
    decided_targets,
    find_workout,
    plan_sync,
    plan_workout,
)
from .progression import Target

EXIT_OK = 0
EXIT_NOTHING_USABLE = 1
EXIT_RATE_LIMITED = 2
EXIT_CONFIG = 3

logger = logging.getLogger(__name__)

#: How `check` shows a finding: the marker that survives a plain run, and the
#: level it is logged at, so severity outlives a redirect of stdout too.
SEVERITY = {
    "error": ("!!", logging.ERROR),
    "warning": (" !", logging.WARNING),
    "note": ("  ", logging.INFO),
}


def describe(spec: ExerciseSpec, target: Target) -> str:
    """Render a target the way the exercise is actually measured."""
    if spec.time_based:
        return f"{target.reps} s"
    if spec.bodyweight:
        return f"{target.reps} reps"
    return f"{target.reps} x {target.weight:g} kg"


def report_change(change: Change, force_flag: str | None = None) -> None:
    flag = force_flag or ("*" if change.moved else " ")
    logger.info(
        f"{flag} {change.spec.name:<40}"
        f" {describe(change.spec, change.old):>13}"
        f"  ->  {describe(change.spec, change.new):<13} ({change.reason})"
    )


def report_plan(plan: Plan, force_flag: str | None = None) -> None:
    for change in plan.changes:
        report_change(change, force_flag)
    for warning in plan.warnings:
        # The marker survives the move to logging: it still sets a warning
        # apart when the level itself is not shown.
        logger.warning(f"  ! {warning}")


def dump(payloads: dict[str, Any], directory: str, suffix: str) -> None:
    for label, payload in payloads.items():
        path = os.path.join(directory, f"dump-{label}-{suffix}.json")
        with open(path, "w") as fh:
            json.dump(payload, fh, indent=2)
        logger.debug(f"Wrote {path}")


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


def command_update(args: argparse.Namespace, config: Config) -> int:
    if args.push and not args.apply:
        logger.error(
            "--push only makes sense with --apply: there is nothing to send yet."
        )
        return EXIT_CONFIG

    session = connect(config.garmin)

    payloads = Payloads(session)
    sessions = pick_sessions(session, config, args.activity)

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

        if args.dump:
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

    if not args.apply:
        logger.info("")
        logger.info(f"Dry run: {updated} step(s) would change. Re-run with --apply.")
        return EXIT_OK

    if not updated:
        logger.info("")
        logger.info("Nothing to write.")
        return EXIT_OK

    # A workout can appear in more than one plan - its own, plus a sync from a
    # later session - but every plan mutated the same payload, so one write
    # carries all of them.
    written: list[Workout] = []
    saved: set[str] = set()
    for each in plans:
        if not each.moved or each.workout.garmin_workout_id in saved:
            continue
        saved.add(each.workout.garmin_workout_id)
        session.save_workout(each.workout.garmin_workout_id, each.payload)
        logger.info(
            f"Wrote {each.workout.key} (workout {each.workout.garmin_workout_id})"
        )
        written.append(each.workout)

    logger.info("")
    logger.info(f"Wrote {updated} updated step(s) to Garmin.")

    if args.push:
        push_to_watch(session, written)

    return EXIT_OK


def sync_other_workouts(
    payloads: Payloads,
    config: Config,
    source: Workout,
    targets: dict[str, Target],
) -> list[Plan]:
    """Propagate decided targets into every other workout that shares them."""
    from .garmin.payloads import normalise  # local: only needed for the lookup

    plans: list[Plan] = []
    for other in config:
        if other.key == source.key:
            continue
        if not any(normalise(s.garmin_name) in targets for s in other.exercises):
            continue

        payload = payloads[other.garmin_workout_id]
        plan = plan_sync(other, payload, targets, source.key)
        if not plan.moved:
            continue

        logger.info("")
        logger.info(f"Also in {other.key} (workout {other.garmin_workout_id}):")
        report_plan(plan, force_flag="*")
        plans.append(plan)

    return plans


def command_fetch(args: argparse.Namespace, config: Config) -> int:
    session = connect(config.garmin)

    ids = args.workout_ids or [w.garmin_workout_id for w in config]
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


def command_list(args: argparse.Namespace, config: Config) -> int:
    session = connect(config.garmin)
    sport = None if args.all else STRENGTH
    workouts = session.list_workouts(sport_type=sport)

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
        if args.all:
            kind = (entry.get("sportType") or {}).get("sportTypeKey", "?")
            name = f"{name}  [{kind}]"
        logger.info(f"{workout_id:<12} {updated:<11} {mark:<3}{name}")

    logger.info("")
    logger.info(f"{len(workouts)} workout(s); * already in your config")
    return EXIT_OK


def _select(session: GarminSession, args: argparse.Namespace) -> list[dict[str, Any]]:
    """The workout summaries an import or check should cover."""
    workouts = session.list_workouts(sport_type=STRENGTH)
    if args.id:
        wanted = [w for w in workouts if str(w.get("workoutId")) == args.id]
        if not wanted:
            raise ActivityNotFound(f"No strength workout with id {args.id}.")
        return wanted
    if args.name:
        needle = args.name.lower()
        wanted = [w for w in workouts if needle in (w.get("workoutName") or "").lower()]
        if not wanted:
            names = ", ".join(repr(w.get("workoutName")) for w in workouts)
            raise ActivityNotFound(
                f"No strength workout matching {args.name!r}. Found: {names}"
            )
        return wanted
    return workouts


def command_import(args: argparse.Namespace, config: Config) -> int:
    session = connect(config.garmin)
    summaries = _select(session, args)

    imported = [
        describe_workout(session.workout(str(s["workoutId"]))) for s in summaries
    ]
    text = render_config(imported)

    if not args.output:
        # Config content, not a report: written straight out so that it stays
        # redirectable and never picks up a log prefix.
        print(text)
        return EXIT_OK

    if os.path.exists(args.output) and not args.force:
        logger.error(f"{args.output} already exists. Pass --force to overwrite it.")
        return EXIT_CONFIG

    with open(args.output, "w") as fh:
        fh.write(text)

    exercises = sum(len(w.exercises) for w in imported)
    logger.info(
        f"Wrote {len(imported)} workout(s), {exercises} exercises -> {args.output}"
    )
    logger.info("Check the TODO comments before using it.")
    return EXIT_OK


def command_check(args: argparse.Namespace, config: Config) -> int:
    session = connect(config.garmin)

    findings: list[Finding] = []
    for workout in config:
        try:
            payload = session.workout(workout.garmin_workout_id)
        except Exception as exc:  # noqa: BLE001 - report and carry on
            detail = f"could not fetch workout {workout.garmin_workout_id}: {exc}"
            logger.error(f"{workout.key}: {detail}")
            # A workout that cannot be read is itself an error-level finding,
            # so an unreachable workout still fails the command.
            findings.append(Finding(workout.key, detail, "error"))
            continue

        found = check_workout(workout, payload)
        logger.info(f"{workout.key} ({workout.garmin_workout_id})")
        if not found:
            logger.info("  ok")
        for finding in found:
            marker, level = SEVERITY[finding.severity]
            logger.log(level, f"  {marker} {finding.detail}")
        logger.info("")
        findings.extend(found)

    serious = [f for f in findings if f.severity != "note"]
    logger.info(f"{len(serious)} issue(s) across {len(config.workouts)} workout(s)")
    return EXIT_NOTHING_USABLE if serious else EXIT_OK


def add_verbose(
    parser: argparse.ArgumentParser, default: Any = argparse.SUPPRESS
) -> None:
    """Accept -v on this parser, so it reads either side of the command.

    argparse copies a subcommand's defaults back over the top-level namespace
    once the subcommand is parsed. Only the top level therefore defaults the
    flag; a subcommand suppresses its own default rather than writing False
    over a -v that was given before the command.
    """
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=default,
        help="show debug output as well",
    )


def build_parser() -> argparse.ArgumentParser:
    # Raw formatting on the top level only: argparse would otherwise reflow the
    # examples into a single paragraph. The subcommands are plain prose, so
    # they keep the default formatter and its wrapping.
    parser = argparse.ArgumentParser(
        prog="workout",
        description="Advance Garmin strength workout targets using double progression.",
        epilog=(
            "examples:\n"
            "  workout update            show what the last session earned\n"
            "  workout update --apply    write those targets back to Garmin\n"
            "  workout update --dump     save the raw Garmin JSON, change nothing\n"
            "  workout update --apply --push   also send them to your watch\n"
            "  workout fetch             download the workout definitions\n"
            "  workout list              show your Garmin workouts and ids\n"
            "  workout import -o f.yaml  build config from Garmin workouts\n"
            "  workout check             report config/Garmin drift\n"
            "\n"
            "Your routine lives in workouts.yaml; copy workouts.example.yaml to\n"
            "get started. Nothing is written to Garmin without --apply."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config", default=DEFAULT_CONFIG, help="path to workouts.yaml"
    )
    add_verbose(parser, default=False)
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")

    update = sub.add_parser(
        "update",
        help="advance targets from the last session",
        description="Read the most recent matching activity, work out the next "
        "target for every exercise, and show the plan. Nothing is sent to "
        "Garmin unless --apply is given. A target that moves is also synced "
        "into any other workout containing that exercise. Editing a workout "
        "does not reach the watch by itself, so --push queues it for the device "
        "to collect on its next sync.",
    )
    update.add_argument("--apply", action="store_true", help="write changes to Garmin")
    update.add_argument(
        "--activity", metavar="ID", help="activity id to use instead of the latest"
    )
    update.add_argument(
        "--dump", action="store_true", help="also save the raw Garmin JSON payloads"
    )
    update.add_argument(
        "--push",
        action="store_true",
        help="queue the updated workouts for your watch (requires --apply)",
    )
    add_verbose(update)
    update.set_defaults(func=command_update)

    fetch = sub.add_parser(
        "fetch",
        help="download workout definitions as JSON",
        description="Save workout definitions as JSON, for inspecting Garmin's "
        "payloads or checking connectivity.",
    )
    fetch.add_argument(
        "workout_ids",
        nargs="*",
        metavar="ID",
        help="workout ids; defaults to every workout in the config",
    )
    add_verbose(fetch)
    fetch.set_defaults(func=command_fetch)

    listing = sub.add_parser(
        "list",
        help="list your Garmin workouts and their ids",
        description="Show the strength workouts in your Garmin account, with "
        "the ids to put in workouts.yaml. Entries already in your config are "
        "marked with an asterisk.",
    )
    listing.add_argument(
        "--all", action="store_true", help="include non-strength workouts"
    )
    add_verbose(listing)
    listing.set_defaults(func=command_list)

    importer = sub.add_parser(
        "import",
        help="generate config from a Garmin workout",
        description="Read workouts built in Garmin Connect and print them as "
        "workouts.yaml content. Garmin stores a single target rather than a rep "
        "range and records no load type, so those are inferred and marked TODO. "
        "Writes to stdout unless -o is given; your config is never modified.",
    )
    picker = importer.add_mutually_exclusive_group()
    picker.add_argument("--name", help="only workouts whose name contains this")
    picker.add_argument("--id", metavar="ID", help="only this workout id")
    importer.add_argument(
        "-o", "--output", metavar="PATH", help="write to this file instead of stdout"
    )
    importer.add_argument(
        "--force", action="store_true", help="overwrite an existing output file"
    )
    add_verbose(importer)
    importer.set_defaults(func=command_import)

    check = sub.add_parser(
        "check",
        help="compare your config against Garmin",
        description="Report where workouts.yaml and the Garmin workouts "
        "disagree: wrong exercise names, differing set counts, and exercises "
        "present in one but not the other. Read-only.",
    )
    add_verbose(check)
    check.set_defaults(func=command_check)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure(args.verbose)

    try:
        config = load_config(args.config)
    except (ConfigError, OSError) as exc:
        logger.error(f"Configuration error: {exc}")
        return EXIT_CONFIG

    try:
        return args.func(args, config)
    except GarminConnectTooManyRequestsError as exc:
        logger.error(f"Rate limited by Garmin: {exc}")
        logger.error("Your IP is temporarily blocked. Wait a while and re-run.")
        return EXIT_RATE_LIMITED
    except ActivityNotFound as exc:
        logger.error(str(exc))
        return EXIT_NOTHING_USABLE


if __name__ == "__main__":
    sys.exit(main())
