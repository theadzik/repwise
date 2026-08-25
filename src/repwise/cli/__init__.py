"""The command line: parse, build what a command needs, run it, map failures.

This is the composition root. Everything concrete is constructed here - the
config is loaded, the Garmin session is opened, an argparse Namespace becomes
the options a use case declares - so that nothing in `app/` has to reach for a
dependency of its own or know what a command line is.
"""

import argparse
import logging
import sys
from collections.abc import Callable

from ..app.checking import run_check
from ..app.fetch import run_fetch, run_fetch_activities, run_fetch_exercises
from ..app.importing import ImportOptions, run_import
from ..app.listing import run_list
from ..app.logout import run_logout
from ..app.update import UpdateOptions, run_update
from ..config import load_config
from ..domain.models import Config
from ..errors import ExitCode, UsageError, WorkoutError
from ..garmin.client import connect
from ..log import configure
from .completion import render
from .parser import ACTIVITIES, CATALOG, TARGETS, build_parser

__all__ = ["main", "build_parser"]

logger = logging.getLogger(__name__)

#: The one command answered without a config, and so the one absent from
#: HANDLERS below. main() says why.
COMPLETION = "completion"

#: An argparse Namespace and a config in, an exit code out. The adapters below
#: are the whole of what the CLI layer does with a command: pick the options
#: out of the namespace, open a session, hand both to the use case.
Handler = Callable[[argparse.Namespace, Config], ExitCode]


def _update(args: argparse.Namespace, config: Config) -> ExitCode:
    options = UpdateOptions(apply=args.apply, activity=args.activity, push=args.push)
    return run_update(connect(config.garmin), config, options)


def _fetch_target(args: argparse.Namespace) -> tuple[str, list[str]]:
    """What `fetch` was asked to download, and which ids narrow it.

    A target can only be the first word, since it says what the ids after it
    are; argparse rejects anything else there. One appearing later is a mistake
    rather than an id, and is refused rather than downloaded as though it were
    one - which argparse cannot do for us, because the ids after the target are
    numbers it has nothing to check against.
    """
    target, ids = args.target, list(args.ids)

    stray = next((word for word in ids if word in TARGETS), None)
    if stray:
        raise UsageError(
            f"`{stray}` says what to download, so it goes first: "
            f"`repwise fetch {stray}`."
        )
    return target, ids


def _fetch(args: argparse.Namespace, config: Config) -> ExitCode:
    target, ids = _fetch_target(args)

    if target == CATALOG:
        if ids:
            # Refused rather than guessed at: the catalog shares a command with
            # the other two downloads and nothing else - different source,
            # different destination, and it alone needs no login - so doing
            # both from one invocation would be a coincidence of spelling.
            raise UsageError(
                f"`fetch {CATALOG}` downloads the exercise catalog and takes no "
                f"workout ids. Run it on its own."
            )
        return run_fetch_exercises(config.garmin)

    # --force is spelled as a session with no cache behind it rather than as a
    # flag the use case reads: a download that must replace what is on disk is
    # one that must not answer from it either, and this way the use case has
    # one path rather than two.
    session = connect(config.garmin, cache=not args.force)
    if target == ACTIVITIES:
        return run_fetch_activities(session, config, ids)
    return run_fetch(session, config, ids)


def _list(args: argparse.Namespace, config: Config) -> ExitCode:
    return run_list(connect(config.garmin), config, every_sport=args.all)


def _import(args: argparse.Namespace, config: Config) -> ExitCode:
    options = ImportOptions(
        name=args.name, id=args.id, output=args.output, force=args.force
    )
    return run_import(connect(config.garmin), options)


def _check(args: argparse.Namespace, config: Config) -> ExitCode:
    return run_check(connect(config.garmin), config)


def _logout(args: argparse.Namespace, config: Config) -> ExitCode:
    # No session: opening one to throw it away would prompt for the password of
    # the account you are asking to be signed out of.
    return run_logout(config.garmin)


#: Keyed by the subparser name, so a command that parses has somewhere to go.
#: `required=True` on the subparsers means an unknown key cannot be reached.
#: `completion` is not among them: it is answered before a config exists, and
#: main() says why.
HANDLERS: dict[str, Handler] = {
    "update": _update,
    "fetch": _fetch,
    "list": _list,
    "import": _import,
    "check": _check,
    "logout": _logout,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure(args.verbose)

    # Answered here rather than through HANDLERS below, because the config is
    # loaded to reach those and this command must run without one: the way to
    # use it is `source <(repwise completion bash)` from a startup file, which
    # runs in whatever directory a shell opened in. The parser it describes is
    # the one that just parsed this, so the script cannot describe a different
    # command line than the binary printing it has.
    if args.command == COMPLETION:
        print(render(parser, args.shell))
        return ExitCode.OK

    # One handler for every failure this tool knows how to describe. Anything
    # else is a bug here, and its traceback is the most useful thing to show.
    try:
        return HANDLERS[args.command](args, load_config(args.config))
    except WorkoutError as exc:
        logger.error(str(exc))
        if exc.advice:
            logger.error(exc.advice)
        return exc.exit_code


if __name__ == "__main__":
    sys.exit(main())
