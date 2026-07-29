"""Shared fixtures and builders."""

import os

import pytest

from workout.domain.models import ExerciseSpec
from workout.domain.progression import PerformedSet

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
#: The shipped example. A user's own workouts.yaml is gitignored, so this
#: is the only config guaranteed to exist in a fresh checkout.
EXAMPLE_CONFIG = os.path.join(REPO_ROOT, "workouts.example.yaml")


def spec(**kwargs) -> ExerciseSpec:
    """An ExerciseSpec with sensible defaults, overridable per test."""
    base = {
        "name": "Barbell Back Squat",
        "garmin_name": "BARBELL_BACK_SQUAT",
        "garmin_category": "SQUAT",
        "rep_low": 6,
        "rep_high": 10,
        "sets": 3,
        "load": "barbell",
        "weight_step": 5.0,
    }
    return ExerciseSpec(**{**base, **kwargs})


def held(*seconds: float) -> list[PerformedSet]:
    """Timed sets as Garmin logs them: 1 rep, real figure in the duration."""
    return [PerformedSet(1, 0.0, s).as_time() for s in seconds]


@pytest.fixture
def write_config(tmp_path):
    """Write a workouts.yaml into a temp dir and return its path."""

    def _write(text: str) -> str:
        path = tmp_path / "workouts.yaml"
        path.write_text(text)
        return str(path)

    return _write


FIXTURE = """
settings:
  weight_steps:
    barbell: 5.0
    dumbbell: 1.0

workouts:
  - key: Workout A
    garmin_workout_id: "123"
    activity_prefixes: ["Training A"]
    exercises:
      - name: Barbell Back Squat
        garmin_name: BARBELL_BACK_SQUAT
        garmin_category: SQUAT
        rep_low: 6
        rep_high: 10
        sets: 4
        rest: 120
        load: barbell
      - name: Plank
        garmin_name: PLANK
        garmin_category: PLANK
        rep_low: 30
        rep_high: 60
        sets: 3
        load: bodyweight
        unit: seconds
"""
