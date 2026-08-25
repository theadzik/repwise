"""What the command line accepts, and the help it prints.

Declarative on purpose: this module builds a parser and nothing else, so the
one file to open to answer "which flags exist" holds no behaviour to read
past. Which function runs is `cli/__init__.py`, keyed by the command name.

The constants below are part of the same statement - words the command line
accepts, rather than things it does - so they live here too. `completion.py`
reads them and this parser to write its scripts.
"""

import argparse
from typing import Any

from .. import __version__

#: What `fetch` can be asked to download, as the first word after it. Safe as
#: keywords rather than flags because a Garmin id is a number, so no id a user
#: could legitimately pass is ambiguous with one of these.
WORKOUTS = "workouts"
ACTIVITIES = "activities"
CATALOG = "exercises"
TARGETS = (WORKOUTS, ACTIVITIES, CATALOG)

#: The shells `completion` knows how to write for. One renderer answers each,
#: in `completion.py`.
SHELLS = ("bash", "zsh")


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
        prog="repwise",
        description="Advance Garmin strength workout targets using double progression.",
        epilog=(
            "examples:\n"
            "  repwise update            show what a run would change\n"
            "  repwise update --apply    write it back to Garmin\n"
            "  repwise update --apply --push   also send them to your watch\n"
            "  repwise update --activity 1234  replay one session you skipped\n"
            "  repwise fetch workouts    download the workout definitions\n"
            "  repwise fetch activities  download your recent strength sessions\n"
            "  repwise fetch exercises   refresh Garmin's exercise catalog\n"
            "  repwise list              show your Garmin workouts and ids\n"
            "  repwise import -o f.yaml  build config from Garmin workouts\n"
            "  repwise check             report config/Garmin drift\n"
            "  repwise logout            forget the cached Garmin session\n"
            "  repwise completion bash   print a shell completion script\n"
            "\n"
            "Your routine lives in workouts.yaml; copy workouts.example.yaml to\n"
            "get started. Nothing is written to Garmin without --apply."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        # argparse decides for itself whether colour is wanted - a terminal,
        # and neither NO_COLOR nor TERM=dumb set - so piped help stays plain.
        # The subcommands inherit it, so it is set once.
        color=True,
        # A mistyped command says what you probably meant rather than only
        # listing the five that exist. Set here alone: it acts on mistyped
        # choices, and the subcommand name is the only choice this parser has.
        suggest_on_error=True,
    )
    # The single source of the number is [project].version in pyproject.toml,
    # which `cz bump` writes through to __init__.py. Worth exposing: the first
    # thing to establish about a report of odd behaviour is which version
    # produced it.
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="show the version and exit",
    )
    # No default for --config: where the config lives depends on how the tool
    # was installed, so config.py works it out rather than the parser freezing
    # one answer into the help text.
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="path to workouts.yaml; by default $REPWISE_CONFIG, then "
        "./workouts.yaml, then ~/.config/repwise/workouts.yaml",
    )
    add_verbose(parser, default=False)
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")

    update = sub.add_parser(
        "update",
        help="advance targets, and bring your workouts in line with the config",
        description="Two jobs in one. Every workout in your config is brought "
        "in line with what the file says - which exercises it holds and in "
        "what order, their sets, their rests, the note on each step - and one "
        "with no garmin_workout_id is built in Garmin, its new id written back "
        "into the file. Then the most recent matching activity of each workout "
        "decides the next target for the exercises it contains. Training A and "
        "then B and running once advances both: the sessions are replayed "
        "oldest first, so the result is the same as having run this after each "
        "of them. Targets follow the weight you actually lifted, but only "
        "while it stays inside the exercise's rep range: come up short of "
        "rep_low on a weight you were not prescribed and the old target is "
        "kept. An exercise the config no longer names is removed, and one it "
        "moves keeps the target stored in it. Nothing is sent to Garmin, and "
        "nothing written back to workouts.yaml, unless --apply is given. A "
        "target that moves is also synced into any other workout containing "
        "that exercise. Editing a workout does not reach the watch by itself, "
        "so --push queues it for the device to collect on its next sync.",
    )
    update.add_argument("--apply", action="store_true", help="write changes to Garmin")
    update.add_argument(
        "--activity",
        metavar="ID",
        help="update from this one activity instead of scanning for each "
        "workout's latest",
    )
    update.add_argument(
        "--push",
        action="store_true",
        help="queue the updated workouts for your watch (requires --apply)",
    )
    add_verbose(update)

    fetch = sub.add_parser(
        "fetch",
        help="download workouts, performed sessions, or Garmin's exercise catalog",
        description="Save what Garmin holds as JSON, for inspecting its "
        "payloads by hand or checking connectivity. The first word says which. "
        "`workouts` downloads the definitions this tool writes targets into, "
        "one file each. `activities` downloads the sessions you performed - "
        "the summary, the sets your watch recorded and the workout each was "
        "run against, three files each - and only strength ones, since the "
        "rest hold no sets to read. Both take ids to narrow them, and both "
        "land in settings.garmin.dump_dir; without ids, `workouts` downloads "
        "every workout in your config and `activities` every strength session "
        "within settings.garmin.activity_search_limit. `exercises` downloads "
        "something else entirely: Garmin's list of every exercise it knows and "
        "the category each is filed under, cached in "
        "settings.garmin.token_store and read by `check` to tell a real "
        "exercise name from a plausible-looking one. `check` downloads that "
        "itself the first time it needs it, so this is how you refresh a copy "
        "that has gone stale, not something to run first.",
    )
    fetch.add_argument(
        "target",
        choices=TARGETS,
        metavar="TARGET",
        help=f"`{WORKOUTS}`, `{ACTIVITIES}` or `{CATALOG}`",
    )
    fetch.add_argument(
        "ids",
        nargs="*",
        metavar="ID",
        help="which workouts or activities to download; defaults to every "
        "workout in the config, or every strength session found",
    )
    fetch.add_argument(
        "--force",
        action="store_true",
        help=f"download sessions already in dump_dir again, rather than "
        f"leaving them alone. Only means anything for `{ACTIVITIES}`, and "
        "only with settings.garmin.activity_caching on",
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
        help="check that your config still names the exercises Garmin holds",
        description="Answer one question: can workouts.yaml still name the "
        "exercises it thinks it is naming? A garmin_name Garmin has never heard "
        "of, one filed under a different category than the config claims, one "
        "that matches nothing in the workout, or one that only matches by "
        "falling back to the category - each is a mistake worth knowing about "
        "before `update` acts on it, since an exercise the config cannot name "
        "is dropped and rebuilt, which costs the target stored in it. Checking "
        "the names needs Garmin's exercise catalog, which is downloaded and "
        "cached the first time and never again unless you ask; without it, the "
        "rest of the checks still run. What `update` would change is "
        "`update`'s own business and is not repeated here. Read-only, and "
        "exits non-zero on any finding at all.",
    )
    add_verbose(check)

    logout = sub.add_parser(
        "logout",
        help="forget the Garmin session cached on this machine",
        description="Delete the OAuth tokens cached in "
        "settings.garmin.token_store, so the next command that reaches Garmin "
        "asks for your email, password and MFA code again. Those tokens are "
        "what a login leaves behind, and until they expire they are as good as "
        "being logged in, so this is what to run on a machine that should stop "
        "having that. The token file is the only thing removed: the exercise "
        "catalog cached beside it is a copy of a public file and is left "
        "alone, which is the difference between this and deleting the "
        "directory by hand. Local only - Garmin issued the token and this does "
        "not hand it back, so it stays valid at Garmin's end until it expires.",
    )
    add_verbose(logout)

    completion = sub.add_parser(
        "completion",
        help="print a shell completion script",
        description="Write a completion script to stdout, so that Tab "
        "finishes commands, options and the files they name. Load it from your "
        "shell's startup file with `source <(repwise completion bash)`, or "
        "redirect it into a completions directory to save doing that on every "
        "shell; under zsh either has to come after `compinit`. The script is "
        "generated from the same parser that prints this help, so it describes "
        "the version of repwise that wrote it - upgrade and it is worth "
        "re-sourcing. Ids are not completed: the only place to look a workout "
        "or activity id up is Garmin, and a login on every press of Tab is not "
        "a feature. Reads no config, opens no session and reaches no network, "
        "which is what makes it safe to run from a startup file in whatever "
        "directory a shell happens to open in.",
    )
    completion.add_argument(
        "shell", choices=SHELLS, help="the shell to write a script for"
    )
    add_verbose(completion)

    return parser
