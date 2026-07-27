"""Which stream a message lands on, and whether it is shown at all."""

import logging

import pytest

from workout.log import PACKAGE, configure

logger = logging.getLogger(f"{PACKAGE}.testing")


@pytest.fixture
def streams(capsys):
    """Configure logging for one test, then put the root logger back.

    `configure` replaces the root handlers, which are global and shared with
    pytest's own capture, so a test that installs them has to undo it.
    """
    root = logging.getLogger()
    handlers, level = root.handlers, root.level
    package_level = logging.getLogger(PACKAGE).level

    def run(verbose: bool = False):
        configure(verbose)
        return capsys.readouterr

    yield run

    root.handlers, root.level = handlers, level
    logging.getLogger(PACKAGE).setLevel(package_level)


def test_the_report_goes_to_stdout(streams):
    """Results are piped and redirected, so they belong on stdout."""
    read = streams()
    logger.info("Dry run: 2 step(s) would change.")
    captured = read()
    assert captured.out == "Dry run: 2 step(s) would change.\n"
    assert captured.err == ""


@pytest.mark.parametrize("level", ["warning", "error"])
def test_problems_go_to_stderr(streams, level):
    """A redirected stdout must not swallow the reason a command failed."""
    read = streams()
    getattr(logger, level)("something is wrong")
    captured = read()
    assert captured.err == "something is wrong\n"
    assert captured.out == ""


def test_debug_is_hidden_by_default(streams):
    read = streams()
    logger.debug("Resumed cached session.")
    assert read().out == ""


def test_verbose_shows_debug_with_its_source(streams):
    read = streams(verbose=True)
    logger.debug("Resumed cached session.")
    out = read().out
    assert "Resumed cached session." in out
    assert "DEBUG" in out and logger.name in out


def test_a_plain_run_shows_the_message_alone(streams):
    """The default output is read by a person, not grepped for levels."""
    read = streams()
    logger.info("Activity: Push A (123)")
    assert read().out == "Activity: Push A (123)\n"


@pytest.mark.parametrize("verbose", [False, True], ids=["plain", "verbose"])
def test_blank_lines_stay_blank(streams, verbose):
    """Reports group their output with blank lines; a prefix would fill them."""
    read = streams(verbose)
    logger.info("")
    assert read().out == "\n"


def test_third_party_debug_stays_quiet_under_verbose(streams):
    """--verbose reports on this tool, not on every HTTP request made."""
    read = streams(verbose=True)
    logging.getLogger("garminconnect").debug("GET /workout-service/workouts")
    captured = read()
    assert captured.out == "" and captured.err == ""
