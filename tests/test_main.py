"""The entry point: what dispatches, what exits with what, what reaches you.

Every other test module calls a `run_*` function directly. This one goes in
through `main()`, which is the only place that turns arguments into a command,
a failure into a status, and a message into a stream.
"""

import argparse
import logging

import pytest
from builders import FIXTURE

from repwise import cli
from repwise.cli import COMPLETION, HANDLERS, build_parser, main
from repwise.errors import ExitCode, GarminError, RateLimited
from repwise.log import PACKAGE


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
    """Adding a subparser without a handler would be a KeyError at runtime.

    `completion` is the documented exception: it is answered before a config
    is loaded, so it is dispatched by name rather than through the table.
    """
    assert command_names(build_parser()) == set(HANDLERS) | {COMPLETION}


#: What a command needs after its name to parse at all. Only `fetch` does.
REQUIRED_WORDS = {"fetch": ["workouts"]}


@pytest.mark.parametrize("command", sorted(HANDLERS))
def test_each_command_reaches_a_handler(command, config, dispatch):
    recorder = dispatch()
    argv = ["--config", config, command, *REQUIRED_WORDS.get(command, [])]

    assert main(argv) == ExitCode.OK
    assert len(recorder.calls) == 1


def test_the_handler_is_given_the_loaded_config(config, dispatch):
    recorder = dispatch()

    main(["--config", config, "check"])

    _, loaded = recorder.calls[0]
    assert set(loaded.workouts) == {"Workout A"}


def test_the_handlers_exit_code_is_the_processs(config, dispatch):
    dispatch(ExitCode.NOTHING_USABLE)
    assert main(["--config", config, "check"]) == ExitCode.NOTHING_USABLE


# --- `completion`, which is dispatched before there is a config -----------


def test_completion_needs_no_config(capsys, tmp_path, monkeypatch):
    """The documented way to use it runs from a shell's startup file.

    That is whatever directory a shell opened in, with no workouts.yaml in it
    and none to be found above it, so needing one would make the command
    useless where it is meant to be used.
    """
    monkeypatch.delenv("REPWISE_CONFIG", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    assert main(["completion", "bash"]) == ExitCode.OK
    assert "complete -F _repwise repwise" in capsys.readouterr().out


def test_completion_opens_no_session(capsys, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    def refuse(settings):
        raise AssertionError("connect() was called")

    monkeypatch.setattr(cli, "connect", refuse)

    assert main(["completion", "zsh"]) == ExitCode.OK
    assert "compdef _repwise repwise" in capsys.readouterr().out


def test_completion_refuses_a_shell_it_cannot_write(capsys):
    """Better than printing a script that would not load."""
    with pytest.raises(SystemExit):
        main(["completion", "fish"])

    assert "invalid choice" in capsys.readouterr().err


# --- `fetch`, which is three downloads sharing a positional ---------------


@pytest.fixture
def fetching(monkeypatch):
    """Record which of the three downloads a `fetch` invocation reached.

    All three are replaced, and so is `connect`: the routing is the whole
    question here, and neither a session nor a network should be needed to
    answer it.
    """
    reached: dict[str, object] = {}

    def workouts(session, config, workout_ids):
        reached["workouts"] = workout_ids
        return ExitCode.OK

    def activities(session, config, activity_ids):
        reached["activities"] = activity_ids
        return ExitCode.OK

    def exercises(settings):
        reached["exercises"] = settings
        return ExitCode.OK

    monkeypatch.setattr(cli, "run_fetch", workouts)
    monkeypatch.setattr(cli, "run_fetch_activities", activities)
    monkeypatch.setattr(cli, "run_fetch_exercises", exercises)

    def connect(settings, cache=True):
        reached["cache"] = cache
        return object()

    monkeypatch.setattr(cli, "connect", connect)
    return reached


def test_fetch_workouts_downloads_every_workout_in_the_config(config, fetching):
    assert main(["--config", config, "fetch", "workouts"]) == ExitCode.OK
    assert fetching["workouts"] == []


def test_fetch_workouts_with_ids_downloads_those(config, fetching):
    assert main(["--config", config, "fetch", "workouts", "123"]) == ExitCode.OK
    assert fetching["workouts"] == ["123"]


def test_fetch_activities_scans_for_them(config, fetching):
    assert main(["--config", config, "fetch", "activities"]) == ExitCode.OK
    assert fetching["activities"] == []


def test_fetch_activities_with_ids_downloads_those(config, fetching):
    assert main(["--config", config, "fetch", "activities", "999"]) == ExitCode.OK
    assert fetching["activities"] == ["999"]


def test_fetch_opens_a_session_that_may_read_dump_dir(config, fetching):
    main(["--config", config, "fetch", "activities"])

    assert fetching["cache"] is True, "the config decides; this does not refuse it"


def test_force_opens_a_session_that_reads_nothing(config, fetching):
    """A download that must replace what is on disk cannot answer from it."""
    main(["--config", config, "fetch", "activities", "--force"])

    assert fetching["cache"] is False


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


def test_a_target_after_another_is_refused(config, fetching, capsys):
    """It says what the ids are, so it cannot be one of them.

    Ours to refuse rather than argparse's: the words after the target are ids,
    which are numbers it has nothing to check against.
    """
    code = main(["--config", config, "fetch", "workouts", "activities"])

    assert code == ExitCode.CONFIG
    assert fetching == {}, "no download should have run"
    assert "goes first" in capsys.readouterr().err


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

    code = main(["--config", config, "fetch", "workouts"])

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
