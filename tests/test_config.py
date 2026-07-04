"""Loading and validating workouts.yaml."""

import pytest
from conftest import EXAMPLE_CONFIG, FIXTURE

from workout.config import ConfigError, load_config

SHARED = """
settings:
  weight_steps:
    machine: 5.0

workouts:
  - key: Workout A
    garmin_workout_id: "1"
    activity_prefixes: ["trening a"]
    exercises:
      - name: Weighted Standing Calf Raise
        garmin_name: WEIGHTED_STANDING_CALF_RAISE
        garmin_category: CALF_RAISE
        rep_low: 12
        rep_high: 20
        sets: 3
        load: machine
  - key: Workout B
    garmin_workout_id: "2"
    activity_prefixes: ["trening b"]
    exercises:
      - name: Weighted Standing Calf Raise
        garmin_name: WEIGHTED_STANDING_CALF_RAISE
        garmin_category: CALF_RAISE
        rep_low: {low}
        rep_high: 20
        sets: 3
        load: machine
"""


def test_loads_fixture(write_config):
    config = load_config(write_config(FIXTURE))
    workout = config["Workout A"]

    assert workout.garmin_workout_id == "123"
    assert workout.activity_prefixes == ["training a"], "prefixes are lower-cased"

    squat, plank = workout.exercises
    assert (squat.rep_low, squat.rep_high, squat.sets) == (6, 10, 4)
    assert squat.weight_step == 5.0
    assert not squat.bodyweight and not squat.time_based

    assert plank.bodyweight and plank.time_based
    assert plank.weight_step == 0.0


def test_garmin_settings_have_defaults(write_config):
    config = load_config(write_config(FIXTURE))
    assert config.garmin.token_store.endswith(".garminconnect")
    assert config.garmin.activity_search_limit == 25


def test_garmin_settings_come_from_the_file(write_config):
    text = FIXTURE.replace(
        "settings:\n",
        "settings:\n  garmin:\n    token_store: /tmp/tokens\n"
        "    activity_search_limit: 5\n",
        1,
    )
    config = load_config(write_config(text))
    assert config.garmin.token_store == "/tmp/tokens"
    assert config.garmin.activity_search_limit == 5


# --- validation -----------------------------------------------------------


def test_unknown_load_is_rejected(write_config):
    bad = FIXTURE.replace("load: barbell", "load: kettlebell")
    with pytest.raises(ConfigError, match="weight_steps"):
        load_config(write_config(bad))


def test_missing_field_is_rejected(write_config):
    bad = FIXTURE.replace("        garmin_name: BARBELL_BACK_SQUAT\n", "")
    with pytest.raises(ConfigError, match="garmin_name"):
        load_config(write_config(bad))


def test_inverted_range_is_rejected(write_config):
    bad = FIXTURE.replace("rep_low: 6", "rep_low: 12")
    with pytest.raises(ConfigError, match="rep_low >= rep_high"):
        load_config(write_config(bad))


def test_duplicate_key_is_rejected(write_config):
    bad = FIXTURE + FIXTURE.split("workouts:")[1]
    with pytest.raises(ConfigError, match="duplicate"):
        load_config(write_config(bad))


def test_rep_step_below_one_is_rejected(write_config):
    bad = FIXTURE.replace("sets: 4", "sets: 4\n        rep_step: 0")
    with pytest.raises(ConfigError, match="rep_step"):
        load_config(write_config(bad))


def test_zero_weight_step_is_rejected(write_config):
    bad = FIXTURE.replace(
        "        load: barbell\n", "        load: barbell\n        weight_step: 0\n", 1
    )
    with pytest.raises(ConfigError, match="never progress"):
        load_config(write_config(bad))


def test_exercise_weight_step_overrides_the_load_type(write_config):
    text = FIXTURE.replace(
        "        load: barbell\n", "        load: barbell\n        weight_step: 7.5\n", 1
    )
    squat = load_config(write_config(text))["Workout A"].exercises[0]
    assert squat.weight_step == 7.5


def test_shared_exercise_with_matching_ranges_is_accepted(write_config):
    config = load_config(write_config(SHARED.format(low=12)))
    assert config.shared_exercises() == {"WEIGHTED_STANDING_CALF_RAISE"}


def test_shared_exercise_with_differing_ranges_is_rejected(write_config):
    """A synced target could otherwise fall outside one workout's range."""
    with pytest.raises(ConfigError, match="different rep ranges"):
        load_config(write_config(SHARED.format(low=8)))


# --- the shipped example --------------------------------------------------


def test_example_config_is_valid():
    config = load_config(EXAMPLE_CONFIG)
    assert set(config.workouts) == {"Workout A", "Workout B"}

    for workout in config:
        assert workout.garmin_workout_id.isdigit()
        assert workout.activity_prefixes
        assert len(workout.exercises) >= 8
        for exercise in workout.exercises:
            assert exercise.sets > 0
            assert exercise.rep_low < exercise.rep_high
            assert exercise.garmin_name.isupper()
            if not exercise.bodyweight:
                assert exercise.weight_step > 0


def test_example_shares_an_exercise_between_workouts():
    assert "WEIGHTED_STANDING_CALF_RAISE" in load_config(EXAMPLE_CONFIG).shared_exercises()


def test_deadlift_steps_by_five():
    """It recruits more musculature than the other barbell lifts."""
    config = load_config(EXAMPLE_CONFIG)
    deadlift = next(
        e for e in config["Workout B"].exercises if e.garmin_name == "BARBELL_DEADLIFT"
    )
    assert deadlift.weight_step == 5.0


def test_lunge_steps_by_two():
    """Counted per side, so it must advance both legs together."""
    config = load_config(EXAMPLE_CONFIG)
    lunge = next(
        e
        for e in config["Workout B"].exercises
        if e.garmin_name == "ALTERNATING_DUMBBELL_LUNGE"
    )
    assert lunge.rep_step == 2
    # Stepping must land exactly on rep_high, not straddle it.
    assert (lunge.rep_high - lunge.rep_low) % lunge.rep_step == 0


def test_missing_default_config_points_at_the_example(tmp_path, monkeypatch):
    """A fresh checkout has only the example, so say how to get started."""
    import workout.config as config_module

    missing = str(tmp_path / "workouts.yaml")
    example = tmp_path / "workouts.example.yaml"
    example.write_text(FIXTURE)
    monkeypatch.setattr(config_module, "DEFAULT_CONFIG", missing)
    monkeypatch.setattr(config_module, "EXAMPLE_CONFIG", str(example))

    with pytest.raises(ConfigError, match="cp workouts.example.yaml workouts.yaml"):
        load_config(missing)


def test_missing_named_config_is_reported_plainly(tmp_path):
    with pytest.raises(ConfigError, match="does not exist"):
        load_config(str(tmp_path / "nope.yaml"))
