"""The command line: parse, build what a command needs, run it, map failures.

This is the composition root. Everything concrete is constructed here - the
config is loaded, the Garmin session is opened, an argparse Namespace becomes
the options a use case declares - so that nothing in `app/` has to reach for a
dependency of its own or know what a command line is.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable

from ..app.checking import run_check
from ..app.fetch import run_fetch
from ..app.importing import ImportOptions, run_import
from ..app.listing import run_list
from ..app.update import UpdateOptions, run_update
from ..config import load_config
from ..domain.models import Config
from ..errors import ExitCode, WorkoutError
from ..garmin.client import connect
from ..log import configure
from .parser import build_parser

__all__ = ["main", "build_parser"]

logger = logging.getLogger(__name__)

#: An argparse Namespace and a config in, an exit code out. The adapters below
#: are the whole of what the CLI layer does with a command: pick the options
#: out of the namespace, open a session, hand both to the use case.
Handler = Callable[[argparse.Namespace, Config], ExitCode]


def _update(args: argparse.Namespace, config: Config) -> ExitCode:
    options = UpdateOptions(
        apply=args.apply, activity=args.activity, dump=args.dump, push=args.push
    )
    return run_update(connect(config.garmin), config, options)


def _fetch(args: argparse.Namespace, config: Config) -> ExitCode:
    return run_fetch(connect(config.garmin), config, args.workout_ids)


def _list(args: argparse.Namespace, config: Config) -> ExitCode:
    return run_list(connect(config.garmin), config, every_sport=args.all)


def _import(args: argparse.Namespace, config: Config) -> ExitCode:
    options = ImportOptions(
        name=args.name, id=args.id, output=args.output, force=args.force
    )
    return run_import(connect(config.garmin), options)


def _check(args: argparse.Namespace, config: Config) -> ExitCode:
    return run_check(connect(config.garmin), config)


#: Keyed by the subparser name, so a command that parses has somewhere to go.
#: `required=True` on the subparsers means an unknown key cannot be reached.
HANDLERS: dict[str, Handler] = {
    "update": _update,
    "fetch": _fetch,
    "list": _list,
    "import": _import,
    "check": _check,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure(args.verbose)

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
