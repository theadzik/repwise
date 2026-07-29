"""What the command line accepts, and the help it prints.

Declarative on purpose: this module builds a parser and nothing else, so the
one file to open to answer "which flags exist" holds no behaviour to read
past. Which function runs is `cli/__init__.py`, keyed by the command name.
"""

from __future__ import annotations

import argparse
from typing import Any


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
            "  workout update            show what your latest sessions earned\n"
            "  workout update --apply    write those targets back to Garmin\n"
            "  workout update --dump     save the raw Garmin JSON, change nothing\n"
            "  workout update --apply --push   also send them to your watch\n"
            "  workout update --activity 1234  replay one session you skipped\n"
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
    # No default: where the config lives depends on how the tool was
    # installed, so config.py works it out rather than the parser freezing one
    # answer into the help text.
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="path to workouts.yaml; by default $WORKOUT_CONFIG, then "
        "./workouts.yaml, then ~/.config/workout/workouts.yaml",
    )
    add_verbose(parser, default=False)
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")

    update = sub.add_parser(
        "update",
        help="advance targets from your latest sessions",
        description="Take the most recent matching activity of every workout in "
        "your config, work out the next target for each exercise, and show the "
        "plan. Training A and then B and running once advances both: the "
        "sessions are replayed oldest first, so the result is the same as "
        "having run this after each of them. Targets follow the weight you "
        "actually lifted, but only while it stays inside the exercise's rep "
        "range: come up short of rep_low on a weight you were not prescribed "
        "and the old target is kept. Each step's notes field is kept showing "
        "how its exercise is programmed, so editing workouts.yaml is on its own "
        "a reason to write. Nothing is sent to Garmin unless --apply "
        "is given. A target that moves is also synced into any other workout "
        "containing that exercise. Editing a workout does not reach the watch "
        "by itself, so --push queues it for the device to collect on its next "
        "sync.",
    )
    update.add_argument("--apply", action="store_true", help="write changes to Garmin")
    update.add_argument(
        "--activity",
        metavar="ID",
        help="update from this one activity instead of scanning for each "
        "workout's latest",
    )
    update.add_argument(
        "--dump",
        action="store_true",
        help="also save the raw Garmin JSON payloads to dump_dir",
    )
    update.add_argument(
        "--push",
        action="store_true",
        help="queue the updated workouts for your watch (requires --apply)",
    )
    add_verbose(update)

    fetch = sub.add_parser(
        "fetch",
        help="download workout definitions as JSON",
        description="Save workout definitions as JSON into "
        "settings.garmin.dump_dir, for inspecting Garmin's payloads or checking "
        "connectivity.",
    )
    fetch.add_argument(
        "workout_ids",
        nargs="*",
        metavar="ID",
        help="workout ids; defaults to every workout in the config",
    )
    add_verbose(fetch)

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

    importer = sub.add_parser(
        "import",
        help="generate config from your Garmin workouts",
        description="Read workouts built in Garmin Connect and print them as "
        "workouts.yaml content. Garmin stores a single target rather than a rep "
        "range and records no load type, so those are inferred and marked TODO. "
        "Writes to stdout unless -o is given; your config is never modified.",
    )
    picker = importer.add_mutually_exclusive_group()
    picker.add_argument(
        "--name", help="only workouts whose name contains this, ignoring case"
    )
    picker.add_argument("--id", metavar="ID", help="only this workout id")
    importer.add_argument(
        "-o", "--output", metavar="PATH", help="write to this file instead of stdout"
    )
    importer.add_argument(
        "--force", action="store_true", help="overwrite an existing output file"
    )
    add_verbose(importer)

    check = sub.add_parser(
        "check",
        help="compare your config against Garmin",
        description="Report where workouts.yaml and the Garmin workouts "
        "disagree: wrong exercise names, differing set counts and rest times, "
        "and exercises present in one but not the other. Read-only, and exits "
        "non-zero if anything worse than a note is found.",
    )
    add_verbose(check)

    return parser
