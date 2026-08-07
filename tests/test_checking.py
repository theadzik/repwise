"""Where `check` gets your bodyweight from."""

from typing import Any

from builders import spec

from workout.app.checking import _bodyweight
from workout.domain.models import Config, Workout
from workout.errors import GarminError

CALF = spec(bodyweight_factor=1.0)
BENCH = spec(name="Barbell Bench Press", garmin_name="BARBELL_BENCH_PRESS")


class ScaleSession:
    """A Garmin account that may or may not have been weighed on."""

    def __init__(self, weight=81.0, failure=None):
        self.weight = weight
        self.failure = failure
        self.asked = 0

    def bodyweight(self, days=30):
        self.asked += 1
        if self.failure:
            raise self.failure
        return self.weight


def configured(*exercises, bodyweight=None) -> Config:
    workout = Workout("Workout A", "1", ["workout a"], list(exercises))
    return Config(workouts={"Workout A": workout}, bodyweight=bodyweight)


def resolved(session: Any, config: Config) -> float | None:
    """The use case takes its session as an argument, so nothing is patched."""
    return _bodyweight(session, config)


def test_garmin_is_asked_when_an_exercise_needs_it():
    session = ScaleSession(weight=81.0)

    assert resolved(session, configured(CALF)) == 81.0
    assert session.asked == 1


def test_what_the_config_states_wins_and_costs_no_request():
    """Someone who wrote it down meant it."""
    session = ScaleSession(weight=81.0)

    assert resolved(session, configured(CALF, bodyweight=75.0)) == 75.0
    assert session.asked == 0


def test_no_request_is_made_when_nothing_would_read_it():
    """Every exercise in the file is loaded by its own weight alone."""
    session = ScaleSession()

    assert resolved(session, configured(BENCH)) is None
    assert session.asked == 0


def test_an_account_that_has_never_weighed_in_reads_as_unknown():
    assert resolved(ScaleSession(weight=None), configured(CALF)) is None


def test_a_failed_weigh_in_read_does_not_fail_the_command():
    """It costs the range checks, which say so themselves, and nothing else."""
    session = ScaleSession(failure=GarminError("nope"))

    assert resolved(session, configured(CALF)) is None
