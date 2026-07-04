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
from .garmin.client import GarminConnectTooManyRequestsError, GarminSession, connect
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


def command_update(args: argparse.Namespace, config: Config) -> int:
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

    for each in plans:
        if not each.moved:
            continue
        session.save_workout(each.workout.garmin_workout_id, each.payload)
        print(f"Wrote {each.workout.key} (workout {each.workout.garmin_workout_id})")

    print(f"\nWrote {updated} updated step(s) to Garmin.")
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
            "  workout fetch             download the workout definitions\n"
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
        "into any other workout containing that exercise.",
    )
    update.add_argument("--apply", action="store_true", help="write changes to Garmin")
    update.add_argument(
        "--activity", metavar="ID", help="activity id to use instead of the latest"
    )
    update.add_argument(
        "--dump", action="store_true", help="also save the raw Garmin JSON payloads"
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
