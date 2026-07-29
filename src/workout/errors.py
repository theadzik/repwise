"""Why a run failed, and what the process exits with.

One hierarchy, and every member carries the status it should exit with. That
is what keeps the contract in a single place: `main()` has one handler, so a
command cannot quietly invent an exit code of its own, and adding a failure
mode means choosing a class rather than remembering a convention.

The codes themselves are documented in docs/commands.md and are part of the
interface - a script wrapping this tool relies on them.

Like `log.py`, this sits outside the layer arrows: a module raises without
knowing who catches, and only `main()` turns an exception into a status.
"""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    OK = 0
    NOTHING_USABLE = 1
    RATE_LIMITED = 2
    CONFIG = 3


class WorkoutError(Exception):
    """A failure the user should see as a message, not as a traceback.

    Anything raised that is not one of these is a bug in this tool, and a
    traceback is the right thing to show for it.
    """

    exit_code: ExitCode = ExitCode.NOTHING_USABLE

    #: A second line, printed after the message: what to do about it. Kept
    #: apart from the message so that the message stays the fact.
    advice: str = ""


class ConfigError(WorkoutError):
    """workouts.yaml is missing, malformed or self-contradictory."""

    exit_code = ExitCode.CONFIG


class UsageError(WorkoutError):
    """The flags given cannot be honoured together."""

    exit_code = ExitCode.CONFIG


class ActivityNotFound(WorkoutError):
    """Nothing in the account matched what the command was asked to work on."""

    exit_code = ExitCode.NOTHING_USABLE


class GarminError(WorkoutError):
    """A call to Garmin Connect failed.

    Raised by `garmin/client.py` alone: saying that talking to Garmin went
    wrong is the adapter's job, so that a caller wanting to carry on past one
    failure can catch this rather than every exception there is.
    """

    exit_code = ExitCode.NOTHING_USABLE


class RateLimited(GarminError):
    """Garmin refused the request because too many have been made."""

    exit_code = ExitCode.RATE_LIMITED
    advice = "Your IP is temporarily blocked. Wait a while and re-run."
