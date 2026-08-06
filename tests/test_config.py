"""Loading and validating workouts.yaml."""

import os
import re

import pytest
from builders import EXAMPLE_CONFIG, FIXTURE

from workout import config as config_module
from workout.config import (
    ConfigError,
    load_config,
    record_workout_id,
    resolve_config,
)

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


def test_the_config_remembers_where_it_was_read_from(write_config):
    """So that a workout id Garmin issues can be written back to that file."""
    path = write_config(FIXTURE)
    assert load_config(path).path == path


def test_rest_between_exercises_is_read_per_workout(write_config):
    text = FIXTURE.replace(
        '    garmin_workout_id: "123"\n',
        '    garmin_workout_id: "123"\n    rest_between_exercises: 45\n',
    )
    assert load_config(write_config(text))["Workout A"].rest_between == 45


def test_no_rest_between_exercises_is_no_opinion(write_config):
    """Absent is not zero: it means leave whatever Garmin already prescribes."""
    assert load_config(write_config(FIXTURE))["Workout A"].rest_between is None


def test_a_negative_rest_between_exercises_is_rejected(write_config):
    text = FIXTURE.replace(
        '    garmin_workout_id: "123"\n',
        '    garmin_workout_id: "123"\n    rest_between_exercises: -30\n',
    )
    with pytest.raises(ConfigError, match="negative rest_between_exercises"):
        load_config(write_config(text))


def test_start_weight_defaults_to_nothing_on_the_bar(write_config):
    config = load_config(write_config(FIXTURE))
    assert config["Workout A"].exercises[0].start_weight == 0.0


def test_start_weight_is_read_per_exercise(write_config):
    text = FIXTURE.replace(
        "        sets: 4\n", "        sets: 4\n        start_weight: 40\n"
    )
    config = load_config(write_config(text))
    assert config["Workout A"].exercises[0].start_weight == 40.0


# --- recording an id Garmin issued ----------------------------------------


COMMENTED = """\
# My routine. Edit the numbers, not the ids.
settings:
  weight_steps:
    barbell: 5.0

workouts:
  # Trained on Mondays.
  - key: Workout A
    activity_prefixes: ["training a"]   # whatever the watch calls it
    exercises:
      - name: Barbell Back Squat
        garmin_name: BARBELL_BACK_SQUAT
        rep_low: 6
        rep_high: 10
        sets: 4
        load: barbell

  - key: Workout B
    garmin_workout_id: "222"
    exercises:
      - name: Barbell Deadlift
        garmin_name: BARBELL_DEADLIFT
        rep_low: 5
        rep_high: 8
        sets: 3
        load: barbell
"""


def test_an_id_is_inserted_into_the_workout_that_earned_it(write_config):
    path = write_config(COMMENTED)

    record_workout_id(path, "Workout A", "1234567")

    assert load_config(path)["Workout A"].garmin_workout_id == "1234567"


def test_recording_an_id_changes_nothing_else_in_the_file(write_config):
    """The file is written by hand: comments, spacing and quoting are the
    user's, and a YAML round trip would quietly rearrange all three."""
    path = write_config(COMMENTED)

    record_workout_id(path, "Workout A", "1234567")

    with open(path) as fh:
        after = fh.read()
    assert after == COMMENTED.replace(
        "  - key: Workout A\n",
        '  - key: Workout A\n    garmin_workout_id: "1234567"\n',
    )


def test_an_existing_id_is_replaced_rather_than_doubled(write_config):
    path = write_config(COMMENTED)

    record_workout_id(path, "Workout B", "999")

    with open(path) as fh:
        after = fh.read()
    assert after.count("garmin_workout_id") == 1
    assert load_config(path)["Workout B"].garmin_workout_id == "999"


def test_recording_refuses_a_workout_it_cannot_find(write_config):
    """Guessing would point two workouts at one Garmin workout."""
    path = write_config(COMMENTED)

    with pytest.raises(ConfigError, match="cannot find the workout entry"):
        record_workout_id(path, "Workout C", "1234567")


def test_a_write_that_fails_leaves_the_config_exactly_as_it_was(
    write_config, monkeypatch
):
    """Opening the file for writing would truncate it first. A run that died
    in between would cost the routine, and the id that stops the workout being
    created all over again."""
    path = write_config(COMMENTED)

    def full(*args, **kwargs):
        raise OSError("No space left on device")

    monkeypatch.setattr(config_module.tempfile, "NamedTemporaryFile", full)

    with pytest.raises(ConfigError, match="could not be written"):
        record_workout_id(path, "Workout A", "1234567")

    with open(path) as fh:
        assert fh.read() == COMMENTED, "not a byte of it lost"


def test_recording_leaves_no_working_file_behind(write_config, tmp_path):
    path = write_config(COMMENTED)

    record_workout_id(path, "Workout A", "1234567")

    assert [each.name for each in tmp_path.iterdir()] == [os.path.basename(path)]


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


# --- reporting every problem at once --------------------------------------


def test_several_problems_are_all_reported(write_config):
    """Fixing one, re-running, and finding the next is the thing to avoid."""
    bad = FIXTURE.replace("rep_low: 6", "rep_low: 12").replace(
        "load: barbell", "load: kettlebell"
    )

    with pytest.raises(ConfigError) as caught:
        load_config(write_config(bad))

    message = str(caught.value)
    assert "2 problems" in message
    assert "rep_low >= rep_high" in message
    assert "weight_steps" in message


def test_problems_in_different_workouts_are_all_reported(write_config):
    """One broken workout must not hide what is wrong with the next."""
    bad = SHARED.format(low=12).replace("sets: 3\n        load: machine", "sets: 3")

    with pytest.raises(ConfigError) as caught:
        load_config(write_config(bad))

    assert str(caught.value).count("Workout") >= 2


def test_a_single_problem_is_reported_on_its_own(write_config):
    """No list, no count: one problem should read as one sentence."""
    bad = FIXTURE.replace("rep_low: 6", "rep_low: 12")

    with pytest.raises(ConfigError) as caught:
        load_config(write_config(bad))

    assert "problems" not in str(caught.value)


def test_a_workout_with_no_id_is_one_to_create_rather_than_a_mistake(write_config):
    """A config may name a workout Garmin has not been told about yet."""
    text = FIXTURE.replace('    garmin_workout_id: "123"\n', "")

    config = load_config(write_config(text))

    assert config["Workout A"].garmin_workout_id is None
    assert config["Workout A"].exercises, "the rest of it loaded normally"


def test_a_workout_with_no_id_still_reports_its_exercises(write_config):
    """One omission at the top should not hide everything below it."""
    bad = FIXTURE.replace('    garmin_workout_id: "123"\n', "").replace(
        "rep_low: 6", "rep_low: 12"
    )

    with pytest.raises(ConfigError, match="rep_low >= rep_high"):
        load_config(write_config(bad))


def test_a_workout_with_neither_an_id_nor_exercises_is_a_mistake(write_config):
    """Nothing to look up in Garmin, and nothing to build there either."""
    text = "workouts:\n  - key: Workout A\n"

    with pytest.raises(ConfigError, match="nothing to find and nothing to create"):
        load_config(write_config(text))


def test_an_empty_file_says_so_rather_than_listing_nothing(write_config):
    with pytest.raises(ConfigError, match="no workouts defined"):
        load_config(write_config("workouts: []\n"))


def test_workouts_must_be_a_list(write_config):
    """A mapping there would otherwise fail deep inside the loop."""
    with pytest.raises(ConfigError, match="should be a list"):
        load_config(write_config("workouts:\n  Workout A:\n    key: A\n"))


def test_exercise_weight_step_overrides_the_load_type(write_config):
    text = FIXTURE.replace(
        "        load: barbell\n",
        "        load: barbell\n        weight_step: 7.5\n",
        1,
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
    assert (
        "WEIGHTED_STANDING_CALF_RAISE" in load_config(EXAMPLE_CONFIG).shared_exercises()
    )


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


def test_missing_named_config_is_reported_plainly(tmp_path):
    with pytest.raises(ConfigError, match="does not exist"):
        load_config(str(tmp_path / "nope.yaml"))


# --- finding the config ---------------------------------------------------


@pytest.fixture
def nowhere(tmp_path, monkeypatch):
    """An empty world: no env var, no config in any searched location."""
    monkeypatch.delenv("WORKOUT_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(config_module, "_CHECKOUT_ROOT", str(tmp_path / "checkout"))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_the_working_directory_is_searched_first(nowhere):
    """Running in a directory holding a routine should just use it."""
    (nowhere / "workouts.yaml").write_text(FIXTURE)
    assert resolve_config() == str(nowhere / "workouts.yaml")


def test_the_env_var_wins_over_the_working_directory(nowhere, monkeypatch):
    (nowhere / "workouts.yaml").write_text(FIXTURE)
    named = nowhere / "elsewhere.yaml"
    named.write_text(FIXTURE)
    monkeypatch.setenv("WORKOUT_CONFIG", str(named))

    assert resolve_config() == str(named)


def test_the_xdg_directory_is_searched_when_the_cwd_has_nothing(nowhere):
    """The place a config belongs once the tool is installed for real."""
    xdg = nowhere / "xdg" / "workout"
    xdg.mkdir(parents=True)
    (xdg / "workouts.yaml").write_text(FIXTURE)

    assert resolve_config() == str(xdg / "workouts.yaml")


def test_an_explicit_path_skips_the_search_entirely(nowhere):
    assert resolve_config("/somewhere/else.yaml") == "/somewhere/else.yaml"


def a_checkout(root):
    """A directory that looks like a clone: a config, beside the example."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "workouts.example.yaml").write_text(FIXTURE)
    (root / "workouts.yaml").write_text(FIXTURE)
    return root


def test_the_checkout_is_searched_last(nowhere):
    """So that `python -m workout` works from anywhere inside a clone."""
    checkout = a_checkout(nowhere / "checkout")
    assert resolve_config() == str(checkout / "workouts.yaml")


def test_a_config_in_the_working_directory_beats_the_checkout(nowhere):
    """A clone of this repo must not shadow the config the user is standing in."""
    a_checkout(nowhere / "checkout")
    (nowhere / "workouts.yaml").write_text(FIXTURE)

    assert resolve_config() == str(nowhere / "workouts.yaml")


def test_an_installed_copy_offers_no_path_inside_site_packages(nowhere):
    """The old code computed one from __file__ and reported only that.

    Installed, that arithmetic lands in a lib directory holding nothing of
    ours, so it is not somewhere to suggest putting a config.
    """
    with pytest.raises(ConfigError) as caught:
        resolve_config()

    message = str(caught.value)
    assert "Looked in:" in message
    assert str(nowhere / "workouts.yaml") in message, "names the obvious place"
    assert "xdg" in message, "and the one it belongs in once installed"
    assert "checkout" not in message, "but not a directory that is not a checkout"


def test_nothing_found_suggests_the_example_when_there_is_one(nowhere):
    """A fresh checkout has only the example, so say how to get started."""
    (nowhere / "workouts.example.yaml").write_text(FIXTURE)

    with pytest.raises(ConfigError, match=re.escape("cp ")):
        resolve_config()


def test_nothing_found_suggests_import_when_there_is_no_example(nowhere):
    """An installed copy ships no example, so point at the command instead."""
    with pytest.raises(ConfigError, match="workout import -o"):
        resolve_config()
