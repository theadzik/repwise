"""Command line interface: argument parsing and output formatting.

The user-facing help text lives in `build_parser()` rather than in this
docstring, so that it can be laid out deliberately.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from .config import DEFAULT_CONFIG, ConfigError, load_config
from .checker import check_workout
from .garmin.client import (
    STRENGTH,
    GarminConnectTooManyRequestsError,
    GarminSession,
    connect,
)
from .importer import describe_workout, render_config
from .garmin.payloads import performed_sets
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


def describe(spec: ExerciseSpec, target: Target) -> str:
    """Render a target the way the exercise is actually measured."""
    if spec.time_based:
        return f"{target.reps} s"
    if spec.bodyweight:
        return f"{target.reps} reps"
    return f"{target.reps} x {target.weight:g} kg"


def print_change(change: Change, force_flag: str | None = None) -> None:
    flag = force_flag or ("*" if change.moved else " ")
    print(
        f"{flag} {change.spec.name:<40}"
        f" {describe(change.spec, change.old):>13}"
        f"  ->  {describe(change.spec, change.new):<13} ({change.reason})"
    )


def print_plan(plan: Plan, force_flag: str | None = None) -> None:
    for change in plan.changes:
        print_change(change, force_flag)
    for warning in plan.warnings:
        print(f"  ! {warning}")


def dump(payloads: dict[str, Any], directory: str, suffix: str) -> None:
    for label, payload in payloads.items():
        path = os.path.join(directory, f"dump-{label}-{suffix}.json")
        with open(path, "w") as fh:
            json.dump(payload, fh, indent=2)
        print(f"Wrote {path}")
    print()


def pick_activity(
    session: GarminSession, config: Config, activity_id: str | None
) -> dict[str, Any]:
    """The activity to learn from: the one asked for, or the latest match."""
    if activity_id:
        return session.activity(activity_id)

    prefixes = [p for w in config for p in w.activity_prefixes]
    for activity in session.recent_activities():
        name = (activity.get("activityName") or "").lower()
        if any(name.startswith(prefix) for prefix in prefixes):
            return activity

    raise ActivityNotFound(
        f"No recent activity matching {prefixes}. "
        "Pass --activity <id> to choose one explicitly."
    )


def push_to_watch(session: GarminSession, workouts: list[Workout]) -> None:
    """Queue each written workout for the watch to collect on its next sync.

    Editing a workout in Garmin Connect does not reach the watch on its own;
    the device only collects a new copy when a message is waiting for it. The
    message goes to the device you last used.
    """
    for workout in workouts:
        session.push_workout(workout.garmin_workout_id)

    print(f"\nQueued {len(workouts)} send(s) to your last-used device.")
    print("Sync your watch to pick up the new targets.")


def command_update(args: argparse.Namespace, config: Config) -> int:
    if args.push and not args.apply:
        print("--push only makes sense with --apply: there is nothing to send yet.")
        return EXIT_CONFIG

    session = connect(config.garmin)

    activity = pick_activity(session, config, args.activity)
    activity_id = str(activity["activityId"])
    activity_name = activity.get("activityName") or ""
    workout = find_workout(config, activity_name)

    print(f"Activity: {activity_name} ({activity_id})")
    print(f"Updating: {workout.key} -> workout {workout.garmin_workout_id}\n")

    sets_payload = session.exercise_sets(activity_id)
    payload = session.workout(workout.garmin_workout_id)

    if args.dump:
        dump(
            {"sets": sets_payload, "workout": payload},
            config.garmin.dump_dir,
            activity_id,
        )

    performed = performed_sets(sets_payload)
    if not any(performed):
        print("No working sets found in that activity; nothing to do.")
        return EXIT_NOTHING_USABLE

    plan = plan_workout(workout, payload, performed)
    print_plan(plan)

    plans = [plan]

    # Anything that moved must move everywhere that exercise appears.
    targets = decided_targets(plan)
    if targets:
        plans.extend(sync_other_workouts(session, config, workout, targets))

    updated = sum(len(p.moved) for p in plans)

    if not args.apply:
        print(f"\nDry run: {updated} step(s) would change. Re-run with --apply.")
        return EXIT_OK

    if not updated:
        print("\nNothing to write.")
        return EXIT_OK

    written: list[Workout] = []
    for each in plans:
        if not each.moved:
            continue
        session.save_workout(each.workout.garmin_workout_id, each.payload)
        print(f"Wrote {each.workout.key} (workout {each.workout.garmin_workout_id})")
        written.append(each.workout)

    print(f"\nWrote {updated} updated step(s) to Garmin.")

    if args.push:
        push_to_watch(session, written)

    return EXIT_OK


def sync_other_workouts(
    session: GarminSession,
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

        payload = session.workout(other.garmin_workout_id)
        plan = plan_sync(other, payload, targets, source.key)
        if not plan.moved:
            continue

        print(f"\nAlso in {other.key} (workout {other.garmin_workout_id}):")
        print_plan(plan, force_flag="*")
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
            print(f"FAILED {workout_id}: {exc}")
            failed = True
            continue

        path = os.path.join(config.garmin.dump_dir, f"workout-{workout_id}.json")
        with open(path, "w") as fh:
            json.dump(payload, fh, indent=2)
        print(f"Saved {payload.get('workoutName', '(unnamed)')} -> {path}")

    return EXIT_NOTHING_USABLE if failed else EXIT_OK


def command_list(args: argparse.Namespace, config: Config) -> int:
    session = connect(config.garmin)
    sport = None if args.all else STRENGTH
    workouts = session.list_workouts(sport_type=sport)

    if not workouts:
        print("No workouts found.")
        return EXIT_NOTHING_USABLE

    known = {w.garmin_workout_id for w in config}
    print(f"{'ID':<12} {'UPDATED':<11} {'':<3}NAME")
    for entry in workouts:
        workout_id = str(entry.get("workoutId"))
        updated = (entry.get("updateDate") or "")[:10]
        mark = "*" if workout_id in known else " "
        name = entry.get("workoutName") or "(unnamed)"
        if args.all:
            kind = (entry.get("sportType") or {}).get("sportTypeKey", "?")
            name = f"{name}  [{kind}]"
        print(f"{workout_id:<12} {updated:<11} {mark:<3}{name}")

    print(f"\n{len(workouts)} workout(s); * already in your config")
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
        print(text)
        return EXIT_OK

    if os.path.exists(args.output) and not args.force:
        print(f"{args.output} already exists. Pass --force to overwrite it.")
        return EXIT_CONFIG

    with open(args.output, "w") as fh:
        fh.write(text)

    exercises = sum(len(w.exercises) for w in imported)
    print(f"Wrote {len(imported)} workout(s), {exercises} exercises -> {args.output}")
    print("Check the TODO comments before using it.")
    return EXIT_OK


def command_check(args: argparse.Namespace, config: Config) -> int:
    session = connect(config.garmin)

    findings = []
    for workout in config:
        try:
            payload = session.workout(workout.garmin_workout_id)
        except Exception as exc:  # noqa: BLE001 - report and carry on
            print(f"{workout.key}: could not fetch workout "
                  f"{workout.garmin_workout_id}: {exc}")
            findings.append(True)
            continue

        found = check_workout(workout, payload)
        print(f"{workout.key} ({workout.garmin_workout_id})")
        if not found:
            print("  ok")
        for finding in found:
            marker = {"error": "!!", "warning": " !", "note": "  "}[finding.severity]
            print(f"  {marker} {finding.detail}")
        print()
        findings.extend(found)

    serious = [f for f in findings if getattr(f, "severity", "error") != "note"]
    print(f"{len(serious)} issue(s) across {len(config.workouts)} workout(s)")
    return EXIT_NOTHING_USABLE if serious else EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    # Raw formatting on the top level only: argparse would otherwise reflow the
    # examples into a single paragraph. The subcommands are plain prose, so
    # they keep the default formatter and its wrapping.
    parser = argparse.ArgumentParser(
        prog="workout",
        description="Advance Garmin strength workout targets using double "
        "progression.",
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
    importer.set_defaults(func=command_import)

    check = sub.add_parser(
        "check",
        help="compare your config against Garmin",
        description="Report where workouts.yaml and the Garmin workouts "
        "disagree: wrong exercise names, differing set counts, and exercises "
        "present in one but not the other. Read-only.",
    )
    check.set_defaults(func=command_check)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        config = load_config(args.config)
    except (ConfigError, OSError) as exc:
        print(f"Configuration error: {exc}")
        return EXIT_CONFIG

    try:
        return args.func(args, config)
    except GarminConnectTooManyRequestsError as exc:
        print(f"Rate limited by Garmin: {exc}")
        print("Your IP is temporarily blocked. Wait a while and re-run.")
        return EXIT_RATE_LIMITED
    except ActivityNotFound as exc:
        print(exc)
        return EXIT_NOTHING_USABLE


if __name__ == "__main__":
    sys.exit(main())
