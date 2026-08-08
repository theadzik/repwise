"""The entry point: what dispatches, what exits with what, what reaches you.

Every other test module calls a `run_*` function directly. This one goes in
through `main()`, which is the only place that turns arguments into a command,
a failure into a status, and a message into a stream.
"""

import argparse
import logging

import pytest
from builders import FIXTURE

from workout import cli
from workout.cli import HANDLERS, build_parser, main
from workout.errors import ExitCode, GarminError, RateLimited
from workout.log import PACKAGE


@pytest.fixture(autouse=True)
def restore_logging():
    """`main` calls configure(), which replaces the global root handlers."""
    root = logging.getLogger()
    handlers, level = root.handlers, root.level
    package_level = logging.getLogger(PACKAGE).level

    yield

    root.handlers, root.level = handlers, level
    logging.getLogger(PACKAGE).setLevel(package_level)


@pytest.fixture
def config(write_config):
    return write_config(FIXTURE)


class Recorder:
    """Stands in for every command: records the call, then does as told."""

    def __init__(self, outcome):
        self.outcome = outcome
        self.calls: list[tuple[argparse.Namespace, object]] = []

    def __call__(self, args, config):
        self.calls.append((args, config))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


@pytest.fixture
def dispatch(monkeypatch):
    """Replace the handlers, so a command runs without touching Garmin."""

    def install(outcome=ExitCode.OK) -> Recorder:
        recorder = Recorder(outcome)
        monkeypatch.setattr(cli, "HANDLERS", dict.fromkeys(HANDLERS, recorder))
        return recorder

    return install


def command_names(parser: argparse.ArgumentParser) -> set[str]:
    """Every subcommand the parser accepts.

    argparse exposes no public accessor for these, and writing the list out
    here by hand would defeat the point of the test that uses it.
    """
    for action in parser._actions:  # noqa: SLF001
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            return set(action.choices)
    raise AssertionError("the parser has no subcommands")


# --- dispatch -------------------------------------------------------------


def test_every_command_that_parses_has_somewhere_to_go():
    """Adding a subparser without a handler would be a KeyError at runtime."""
    assert command_names(build_parser()) == set(HANDLERS)


@pytest.mark.parametrize("command", sorted(HANDLERS))
def test_each_command_reaches_a_handler(command, config, dispatch):
    recorder = dispatch()

    assert main(["--config", config, command]) == ExitCode.OK
    assert len(recorder.calls) == 1


def test_the_handler_is_given_the_loaded_config(config, dispatch):
    recorder = dispatch()

    main(["--config", config, "check"])

    _, loaded = recorder.calls[0]
    assert set(loaded.workouts) == {"Workout A"}


def test_the_handlers_exit_code_is_the_processs(config, dispatch):
    dispatch(ExitCode.NOTHING_USABLE)
    assert main(["--config", config, "check"]) == ExitCode.NOTHING_USABLE


# --- `fetch`, which is two downloads sharing a positional -----------------


@pytest.fixture
def fetching(monkeypatch):
    """Record which of the two downloads a `fetch` invocation reached.

    Both are replaced, and so is `connect`: the routing is the whole question
    here, and neither a session nor a network should be needed to answer it.
    """
    reached: dict[str, object] = {}

    def workouts(session, config, workout_ids):
        reached["workouts"] = workout_ids
        return ExitCode.OK

    def exercises(settings):
        reached["exercises"] = settings
        return ExitCode.OK

    monkeypatch.setattr(cli, "run_fetch", workouts)
    monkeypatch.setattr(cli, "run_fetch_exercises", exercises)
    monkeypatch.setattr(cli, "connect", lambda settings: object())
    return reached


def test_fetch_with_ids_downloads_workouts(config, fetching):
    assert main(["--config", config, "fetch", "123"]) == ExitCode.OK
    assert fetching == {"workouts": ["123"]}


def test_fetch_with_nothing_downloads_workouts(config, fetching):
    assert main(["--config", config, "fetch"]) == ExitCode.OK
    assert fetching == {"workouts": []}


def test_fetch_exercises_downloads_the_catalog(config, fetching):
    assert main(["--config", config, "fetch", "exercises"]) == ExitCode.OK
    assert "workouts" not in fetching
    assert fetching["exercises"].token_store


def test_fetch_exercises_opens_no_session(config, monkeypatch, fetching):
    """A public file should not cost a password prompt on a first run."""

    def refuse(settings):
        raise AssertionError("connect() was called")

    monkeypatch.setattr(cli, "connect", refuse)

    assert main(["--config", config, "fetch", "exercises"]) == ExitCode.OK


def test_mixing_the_catalog_with_workout_ids_is_refused(config, fetching, capsys):
    """Different source, different destination, and one of them needs a login."""
    code = main(["--config", config, "fetch", "exercises", "123"])

    assert code == ExitCode.CONFIG
    assert fetching == {}, "neither download should have run"
    assert "takes no workout ids" in capsys.readouterr().err


# --- failures -------------------------------------------------------------


def test_a_missing_config_is_reported_and_exits_three(capsys, tmp_path):
    code = main(["--config", str(tmp_path / "nope.yaml"), "check"])

    assert code == ExitCode.CONFIG
    assert "does not exist" in capsys.readouterr().err


def test_a_bad_config_never_reaches_the_command(write_config, dispatch):
    recorder = dispatch()
    broken = write_config(FIXTURE.replace("rep_low: 6", "rep_low: 12"))

    assert main(["--config", broken, "update"]) == ExitCode.CONFIG
    assert recorder.calls == [], "the command was not run"


def test_a_failure_exits_with_the_code_it_carries(config, dispatch, capsys):
    dispatch(RateLimited("Rate limited by Garmin: 429"))

    code = main(["--config", config, "update"])

    assert code == ExitCode.RATE_LIMITED
    assert "Rate limited by Garmin" in capsys.readouterr().err


def test_advice_is_printed_after_the_message(config, dispatch, capsys):
    """Rate limiting is only actionable if the reader is told to wait."""
    dispatch(RateLimited("Rate limited by Garmin: 429"))

    main(["--config", config, "update"])

    assert "Wait a while" in capsys.readouterr().err


def test_a_failure_with_no_advice_prints_only_the_message(config, dispatch, capsys):
    dispatch(GarminError("Could not fetch the workout: timed out"))

    code = main(["--config", config, "fetch"])

    assert code == ExitCode.NOTHING_USABLE
    assert capsys.readouterr().err.strip() == "Could not fetch the workout: timed out"


def test_problems_go_to_stderr_not_stdout(config, dispatch, capsys):
    """So a redirected report does not swallow the reason it failed."""
    dispatch(GarminError("Could not list your workouts: timed out"))

    main(["--config", config, "list"])

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "timed out" in captured.err


def test_an_unexpected_error_is_not_swallowed(config, dispatch):
    """A bug in this tool should surface as a traceback, not as an exit code."""
    dispatch(ValueError("a bug, not a failure the user can act on"))

    with pytest.raises(ValueError):
        main(["--config", config, "check"])


# --- verbosity ------------------------------------------------------------


def test_verbose_turns_on_debug_for_this_package_only(config, dispatch):
    dispatch()

    main(["--config", config, "-v", "check"])

    assert logging.getLogger(PACKAGE).level == logging.DEBUG
    assert logging.getLogger("garminconnect").level == logging.NOTSET


def test_verbose_after_the_command_works_too(config, dispatch):
    """argparse copies subcommand defaults back over the top-level namespace."""
    dispatch()

    main(["--config", config, "check", "--verbose"])

    assert logging.getLogger(PACKAGE).level == logging.DEBUG
