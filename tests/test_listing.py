"""What `list` shows: the ids workouts.yaml needs, and which it already has.

The command exists to answer one question - "what is the id of the workout I
just built in Connect?" - so the id, and the mark saying the config already
knows it, are the two things every test here is about.
"""

import logging
from typing import Any

from builders import spec

from repwise.app.listing import run_list
from repwise.domain.models import Config, Workout
from repwise.errors import ExitCode
from repwise.garmin.client import STRENGTH


class Account:
    """The workouts an account holds, and what it was asked for."""

    def __init__(self, *workouts):
        self.workouts = list(workouts)
        self.asked = []

    def list_workouts(self, sport_type=STRENGTH):
        self.asked.append(sport_type)
        return self.workouts


def account(*summaries) -> Any:  # noqa: ANN401
    """`Any` rather than the class, so that a stand-in goes where a real
    session is asked for without a `cast` at every call site."""
    return Account(*summaries)


def summary(workout_id, name="Workout A", updated="2026-09-05T18:00:00.0", sport=None):
    entry = {"workoutId": workout_id, "workoutName": name, "updateDate": updated}
    if sport:
        entry["sportType"] = {"sportTypeKey": sport}
    return entry


def configured(*ids) -> Config:
    """A config holding these Garmin ids, so `list` can mark them as known."""
    return Config(
        workouts={
            f"Workout {n}": Workout(f"Workout {n}", each, [], [spec()])
            for n, each in enumerate(ids)
        }
    )


def listed(account, config=None, caplog=None, **kwargs) -> str:
    """What the command printed, without the prefixes caplog.text adds.

    The alignment of a row is part of what `list` is for, so the raw messages
    are what a test should read.
    """
    with caplog.at_level(logging.INFO, logger="repwise.app.listing"):
        assert run_list(account, config or Config(workouts={}), **kwargs) == ExitCode.OK
    return "\n".join(caplog.messages)


def test_the_id_and_the_name_are_both_shown(caplog):
    """The id is what the config needs; the name is how you recognise it."""
    said = listed(account(summary("123", "Push Day")), caplog=caplog)

    assert "123" in said
    assert "Push Day" in said


def row(said: str, workout_id: str) -> str:
    """The listed line for one workout, apart from the header and the legend."""
    return next(line for line in said.splitlines() if line.startswith(workout_id))


def test_a_workout_the_config_already_holds_is_marked(caplog):
    said = listed(account(summary("123")), configured("123"), caplog=caplog)

    assert "*" in row(said, "123")
    assert "already in your config" in said


def test_a_workout_the_config_does_not_hold_is_not_marked(caplog):
    """The mark is the whole point: it separates what is left to add."""
    said = listed(account(summary("456")), configured("123"), caplog=caplog)

    assert "*" not in row(said, "456")


def test_only_strength_workouts_are_asked_for_by_default():
    """A running workout has no id workouts.yaml could use."""
    account_ = account(summary("123"))

    run_list(account_, Config(workouts={}))

    assert account_.asked == [STRENGTH]


def test_every_sport_asks_for_all_of_them(caplog):
    account_ = account(summary("123", sport="running"))

    said = listed(account_, caplog=caplog, every_sport=True)

    assert account_.asked == [None]
    assert "running" in said, "the sport is what makes a mixed list readable"


def test_an_empty_account_is_reported_rather_than_printed_empty(caplog):
    """Nothing to list is a dead end, so it exits non-zero and says why."""
    with caplog.at_level(logging.WARNING, logger="repwise.app.listing"):
        assert run_list(account(), Config(workouts={})) == ExitCode.NOTHING_USABLE

    assert "No workouts found" in caplog.text


def test_a_workout_with_no_name_is_still_listed(caplog):
    """Garmin allows one, and its id is exactly what a `list` is wanted for."""
    said = listed(account(summary("123", name=None)), caplog=caplog)

    assert "123" in said
    assert "(unnamed)" in said


def test_the_count_says_how_many_there_were(caplog):
    said = listed(account(summary("1"), summary("2"), summary("3")), caplog=caplog)

    assert "3 workout(s)" in said
