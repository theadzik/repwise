"""Where the output of a command goes, and how much of it there is.

Levels carry meaning here rather than decoration. INFO is the report the user
asked for, so it goes to stdout and pipes cleanly. WARNING and above are
problems, so they go to stderr and stay visible when stdout is redirected.
DEBUG is the machinery - session resumption, dump paths - and is hidden unless
`--verbose` is given.

Only this package's logger is turned up by `--verbose`. Third-party libraries
keep their own level, so a verbose run reports on this tool rather than on
every HTTP request `garminconnect` makes.
"""

from __future__ import annotations

import logging
import sys

#: What a message looks like by default: the message, and nothing else. The
#: output is a report a person reads, not a log file.
PLAIN = "%(message)s"

#: Under --verbose the level and source earn their space, because the point of
#: the flag is to see where a line came from.
DETAILED = "%(levelname)-7s %(name)s: %(message)s"

PACKAGE = __name__.split(".")[0]


class Formatter(logging.Formatter):
    """The configured format, except for blank lines.

    Reports use blank lines to group their output. A level and a logger name
    in front of an empty line would defeat that, so an empty message stays
    empty however verbose the run is.
    """

    def format(self, record: logging.LogRecord) -> str:
        if not record.getMessage():
            return ""
        return super().format(record)


class MaxLevel(logging.Filter):
    """Pass records at or below `level`, so stdout does not echo stderr."""

    def __init__(self, level: int) -> None:
        super().__init__()
        self.level = level

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno <= self.level


def configure(verbose: bool = False) -> None:
    """Install the handlers. Called once, before anything logs."""
    formatter = Formatter(DETAILED if verbose else PLAIN)

    # A record is filtered by the logger it was made on, not by the loggers it
    # passes on the way to a handler. Handlers can therefore live on the root
    # while the levels that matter are set per package below.
    report = logging.StreamHandler(sys.stdout)
    report.setFormatter(formatter)
    report.addFilter(MaxLevel(logging.INFO))

    problems = logging.StreamHandler(sys.stderr)
    problems.setFormatter(formatter)
    problems.setLevel(logging.WARNING)

    root = logging.getLogger()
    root.handlers = [report, problems]
    root.setLevel(logging.WARNING)

    logging.getLogger(PACKAGE).setLevel(logging.DEBUG if verbose else logging.INFO)
