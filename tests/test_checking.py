"""What `check` gathers before it can check anything: bodyweight, and names."""

import logging
from typing import Any

import pytest
from builders import CATALOG, payload, rep_step, repeat, spec

from repwise.app.checking import _bodyweight, _catalog, run_check
from repwise.domain.models import Config, GarminSettings, Workout
from repwise.errors import ExitCode, GarminError, NotInGarmin
from repwise.garmin import catalog

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


# --- and where it gets Garmin's exercise list from -------------------------


@pytest.fixture
def cataloged(monkeypatch):
    """Answer the catalog lookup, without a cache or a network anywhere."""

    def install(outcome=CATALOG):
        calls = []

        def fake_ensure(settings):
            calls.append(settings)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        monkeypatch.setattr(catalog, "ensure", fake_ensure)
        return calls

    return install


def test_the_catalog_is_fetched_for_the_first_run_that_wants_it(cataloged):
    """A check that only works after another command is a check that goes unrun."""
    calls = cataloged()
    settings = GarminSettings(token_store="/nowhere")

    assert _catalog(settings) is CATALOG
    assert calls == [settings]


def test_an_unreachable_catalog_costs_the_name_checks_and_nothing_else(cataloged):
    cataloged(GarminError("no network"))

    assert _catalog(GarminSettings()) is None


def test_it_says_out_loud_that_the_names_went_unchecked(cataloged, caplog):
    """And names the command that retries it, rather than only the failure."""
    cataloged(GarminError("no network"))

    _catalog(GarminSettings())

    assert "exercise names were not checked" in caplog.text
    assert "repwise fetch exercises" in caplog.text


# --- the command, end to end ----------------------------------------------

SQUAT = repeat(rep_step("BARBELL_BACK_SQUAT", "SQUAT", 6, 30.0), sets=3, rest=90.0)


class FakeSession:
    """A Garmin account holding one workout, and no weigh-ins."""

    def __init__(self, workouts=None):
        self.workouts = workouts if workouts is not None else {"1": payload(SQUAT)}

    def bodyweight(self, days=30):
        return None

    def workout(self, workout_id):
        if workout_id not in self.workouts:
            raise GarminError(f"no workout {workout_id}")
        return self.workouts[workout_id]


def checked(config: Config) -> ExitCode:
    """The use case takes its session as an argument, so nothing is patched."""
    session: Any = FakeSession()
    return run_check(session, config)


def test_a_config_that_lines_up_passes(cataloged):
    cataloged()
    assert checked(configured(spec(sets=3, rest=90))) == ExitCode.OK


def test_an_invented_exercise_fails_the_command(cataloged):
    cataloged()
    invented = spec(name="Nonsense", garmin_name="WAT", garmin_category="SQUAT")

    assert checked(configured(invented)) == ExitCode.NOTHING_USABLE


def test_names_are_checked_for_a_workout_garmin_does_not_have_yet(cataloged, caplog):
    """The whole point of asking the catalog: before the workout is built."""
    caplog.set_level(logging.INFO)
    cataloged()
    invented = spec(name="Nonsense", garmin_name="WAT", garmin_category="SQUAT")
    workout = Workout("Workout A", None, ["workout a"], [invented])
    config = Config(workouts={"Workout A": workout})

    assert checked(config) == ExitCode.NOTHING_USABLE
    assert "not in Garmin yet" in caplog.text
    assert "WAT is not an exercise Garmin has" in caplog.text


def test_a_workout_not_in_garmin_yet_with_good_names_says_so(cataloged, caplog):
    """Silence here would read as "not checked" rather than "checked, and fine"."""
    caplog.set_level(logging.INFO)
    cataloged()
    workout = Workout("Workout A", None, ["workout a"], [spec()])
    config = Config(workouts={"Workout A": workout})

    assert checked(config) == ExitCode.OK
    assert "  ok" in caplog.text


def test_the_other_checks_still_run_without_a_catalog(cataloged, caplog):
    """Worth running with no network at all."""
    cataloged(GarminError("no network"))
    wrong = spec(garmin_name="WEIGHTED_BARBELL_BACK_SQUAT", sets=3, rest=90)

    assert checked(configured(wrong)) == ExitCode.NOTHING_USABLE
    assert "different exercises" in caplog.text


class DeletedInConnect(FakeSession):
    """An account where the id in workouts.yaml no longer names anything."""

    def workout(self, workout_id):
        raise NotInGarmin("Could not fetch the workout: your account has nothing")


def test_an_id_garmin_does_not_have_says_what_to_do_about_it(cataloged, caplog):
    """A 404 is the one failure here with a fix, so it is reported as one."""
    cataloged()
    workout = Workout("Workout A", "404", ["workout a"], [spec()])
    session: Any = DeletedInConnect()

    outcome = run_check(session, Config(workouts={"Workout A": workout}))

    assert outcome == ExitCode.NOTHING_USABLE
    assert "404 is not in your Garmin account" in caplog.text
    assert "delete the id" in caplog.text, "how to have it created again"


def test_an_unreachable_workout_is_still_an_error(cataloged, caplog):
    cataloged()
    workout = Workout("Workout A", "404", ["workout a"], [spec()])

    outcome = checked(Config(workouts={"Workout A": workout}))

    assert outcome == ExitCode.NOTHING_USABLE
    assert "could not fetch workout 404" in caplog.text
