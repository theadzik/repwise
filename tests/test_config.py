"""Loading and validating workouts.yaml."""

import logging
import os
import re

import pytest
from builders import EXAMPLE_CONFIG, FIXTURE

from repwise import config as config_module
from repwise import yamlio
from repwise.config import (
    ConfigError,
    default_dump_dir,
    default_token_store,
    load_config,
    record_workout_id,
    resolve_config,
)
from repwise.domain.models import GarminSettings

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


def test_notes_are_free_text(write_config):
    """Anything the user wants beside the exercise, read verbatim."""
    text = FIXTURE.replace(
        "        sets: 4\n",
        "        sets: 4\n        notes: 'Bar high, sit back. youtu.be/abc'\n",
    )
    squat = load_config(write_config(text))["Workout A"].exercises[0]
    assert squat.notes == "Bar high, sit back. youtu.be/abc"


def test_notes_are_optional(write_config):
    assert load_config(write_config(FIXTURE))["Workout A"].exercises[0].notes is None


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
        notes: Knees out, chest up

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


def test_recording_an_id_leaves_every_other_value_alone(write_config):
    """The file is re-dumped, so what must survive is the programming - every
    workout, every exercise and every number in them."""
    path = write_config(COMMENTED)
    before = load_config(path)

    record_workout_id(path, "Workout A", "1234567")

    after = load_config(path)
    assert list(after.workouts) == list(before.workouts)
    for key, workout in before.workouts.items():
        assert after[key].exercises == workout.exercises
        assert after[key].activity_prefixes == workout.activity_prefixes
    assert after["Workout B"].garmin_workout_id == "222", "the other id is untouched"


def test_a_recorded_id_stays_a_string(write_config):
    """Dumped bare it would read back as an integer, and Garmin's ids are
    long enough to be worth keeping as text."""
    path = write_config(COMMENTED)

    record_workout_id(path, "Workout A", "1234567")

    with open(path) as fh:
        assert "garmin_workout_id: '1234567'" in fh.read()


def test_a_recorded_id_is_written_under_the_key_it_belongs_to(write_config):
    """Appended at the end of the entry it would land under the exercises,
    nowhere near the ids the user wrote by hand."""
    path = write_config(COMMENTED)

    record_workout_id(path, "Workout A", "1234567")

    with open(path) as fh:
        lines = [line.strip() for line in fh if line.strip()]
    assert lines[lines.index("- key: Workout A") + 1].startswith("garmin_workout_id:")


def test_an_existing_id_is_replaced_rather_than_doubled(write_config):
    path = write_config(COMMENTED)

    record_workout_id(path, "Workout B", "999")

    with open(path) as fh:
        after = fh.read()
    assert after.count("garmin_workout_id") == 1
    assert load_config(path)["Workout B"].garmin_workout_id == "999"


def test_the_users_own_notes_survive_a_recorded_id(write_config):
    """The round trip writes back everything it read, free text included."""
    path = write_config(COMMENTED)

    record_workout_id(path, "Workout A", "1234567")

    squat = load_config(path)["Workout A"].exercises[0]
    assert squat.notes == "Knees out, chest up"


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

    monkeypatch.setattr(yamlio.tempfile, "NamedTemporaryFile", full)

    with pytest.raises(ConfigError, match="could not be written"):
        record_workout_id(path, "Workout A", "1234567")

    with open(path) as fh:
        assert fh.read() == COMMENTED, "not a byte of it lost"


def test_recording_leaves_no_working_file_behind(write_config, tmp_path):
    path = write_config(COMMENTED)

    record_workout_id(path, "Workout A", "1234567")

    assert [each.name for each in tmp_path.iterdir()] == [os.path.basename(path)]


@pytest.fixture
def clean_home(tmp_path, monkeypatch):
    """A home directory with no token store of any kind in it.

    Both the default store and the one it fell back to are computed from $HOME,
    so a test that does not say what home is reads whatever the machine running
    it happens to have logged in to.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    return home


def test_garmin_settings_have_defaults(write_config, clean_home):
    """Tokens land beside the config, not in a dot-directory of their own."""
    config = load_config(write_config(FIXTURE))

    assert config.garmin.token_store == str(clean_home / ".config" / "repwise")
    assert config.garmin.activity_search_limit == 50
    assert config.garmin.activity_caching is False, "reading a copy is opt-in"
    assert config.garmin.dump_dir == str(
        clean_home / ".local" / "share" / "repwise" / "dumps"
    )


def test_the_default_token_store_follows_the_config_home(
    write_config, clean_home, monkeypatch
):
    """Move the config directory and the tokens should not stay behind."""
    monkeypatch.setenv("XDG_CONFIG_HOME", "/elsewhere")

    config = load_config(write_config(FIXTURE))

    assert config.garmin.token_store == os.path.join("/elsewhere", "repwise")


def test_the_default_dump_dir_follows_the_data_home(
    write_config, clean_home, monkeypatch
):
    """Same as the store, and for the same reason: $XDG_DATA_HOME is an answer."""
    monkeypatch.setenv("XDG_DATA_HOME", "/elsewhere")

    config = load_config(write_config(FIXTURE))

    assert config.garmin.dump_dir == os.path.join("/elsewhere", "repwise", "dumps")


def test_the_dump_dir_is_the_data_home_and_not_the_cache_home(clean_home):
    """A cache is somewhere anything may be deleted at any time.

    What lands here is the only copy of a session once Garmin's search window
    has moved past it, which is the whole point of `activity_caching`, so it
    goes where data goes.
    """
    assert ".cache" not in default_dump_dir()
    assert str(clean_home / ".local" / "share") in default_dump_dir()


def test_a_declared_store_is_never_second_guessed(write_config, clean_home):
    """Naming one is an instruction, not an opening bid."""
    text = FIXTURE.replace(
        "settings:\n", "settings:\n  garmin:\n    token_store: /tmp/tokens\n", 1
    )

    config = load_config(write_config(text))

    assert config.garmin.token_store == "/tmp/tokens"


def test_the_two_statements_of_the_default_store_cannot_drift(monkeypatch):
    """The default is written down twice, so pin the copies to each other.

    `GarminSettings` states it as a literal, because a frozen dataclass needs
    one and `domain/` may not import `config.py` to compute it. `config.py`
    states it again as the one that resolves $XDG_CONFIG_HOME. With that unset
    they are the same directory, and this fails the day only one of them moves.
    """
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    stated = os.path.expanduser(GarminSettings().token_store)

    assert stated == default_token_store()


def test_the_two_statements_of_the_default_dump_dir_cannot_drift(monkeypatch):
    """Written down twice for the reason the store is, and pinned the same way."""
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)

    stated = os.path.expanduser(GarminSettings().dump_dir)

    assert stated == default_dump_dir()


def test_garmin_settings_come_from_the_file(write_config):
    text = FIXTURE.replace(
        "settings:\n",
        "settings:\n  garmin:\n    token_store: /tmp/tokens\n"
        "    activity_search_limit: 5\n    activity_caching: true\n",
        1,
    )
    config = load_config(write_config(text))
    assert config.garmin.token_store == "/tmp/tokens"
    assert config.garmin.activity_search_limit == 5
    assert config.garmin.activity_caching is True


def test_caching_turned_off_by_hand_is_not_read_as_unset(write_config):
    """`false` is a value, and `or` would read it as an absent one."""
    text = FIXTURE.replace(
        "settings:\n",
        "settings:\n  garmin:\n    activity_caching: false\n",
        1,
    )

    assert load_config(write_config(text)).garmin.activity_caching is False


def test_partial_progression_is_on_unless_the_file_says_otherwise(write_config):
    """The behaviour every config written before the setting existed has."""
    config = load_config(write_config(FIXTURE))

    assert all(
        spec.partial_progression for workout in config for spec in workout.exercises
    )


def test_partial_progression_reaches_every_exercise(write_config):
    """Declared once, and resolved onto the specs the rules actually read."""
    text = FIXTURE.replace(
        "settings:\n", "settings:\n  partial_progression: false\n", 1
    )
    config = load_config(write_config(text))

    assert not any(
        spec.partial_progression for workout in config for spec in workout.exercises
    )


def test_partial_progression_must_be_a_boolean(write_config):
    text = FIXTURE.replace(
        "settings:\n", "settings:\n  partial_progression: 'false'\n", 1
    )

    with pytest.raises(ConfigError, match=r"settings\.partial_progression"):
        load_config(write_config(text))


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
        assert workout.garmin_workout_id is not None
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
    monkeypatch.delenv("REPWISE_CONFIG", raising=False)
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
    monkeypatch.setenv("REPWISE_CONFIG", str(named))

    assert resolve_config() == str(named)


def test_the_xdg_directory_is_searched_when_the_cwd_has_nothing(nowhere):
    """The place a config belongs once the tool is installed for real."""
    xdg = nowhere / "xdg" / "repwise"
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
    """So that `python -m repwise` works from anywhere inside a clone."""
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
    with pytest.raises(ConfigError, match="repwise import -o"):
        resolve_config()


# --- how light a load can go ----------------------------------------------


def test_min_weights_come_from_the_load_type(write_config):
    text = FIXTURE.replace(
        "  weight_steps:",
        "  min_weights:\n    barbell: 12.0\n\n  weight_steps:",
    )
    config = load_config(write_config(text))
    assert config["Workout A"].exercises[0].min_weight == 12.0


def test_an_exercise_can_set_its_own_min_weight(write_config):
    text = FIXTURE.replace(
        "        load: barbell", "        load: barbell\n        min_weight: 20.0"
    )
    config = load_config(write_config(text))
    assert config["Workout A"].exercises[0].min_weight == 20.0


def test_no_min_weights_at_all_means_no_floor(write_config):
    """A config written before deloads existed keeps loading."""
    config = load_config(write_config(FIXTURE))
    assert config["Workout A"].exercises[0].min_weight == 0.0


def test_a_negative_min_weight_is_rejected(write_config):
    text = FIXTURE.replace(
        "        load: barbell", "        load: barbell\n        min_weight: -5"
    )
    with pytest.raises(ConfigError, match="negative min_weight"):
        load_config(write_config(text))


# --- how heavy a load can go ----------------------------------------------


def test_max_weights_come_from_the_load_type(write_config):
    text = FIXTURE.replace(
        "  weight_steps:",
        "  max_weights:\n    barbell: 100.0\n\n  weight_steps:",
    )
    config = load_config(write_config(text))
    assert config["Workout A"].exercises[0].max_weight == 100.0


def test_an_exercise_can_set_its_own_max_weight(write_config):
    text = FIXTURE.replace(
        "        load: barbell", "        load: barbell\n        max_weight: 60.0"
    )
    config = load_config(write_config(text))
    assert config["Workout A"].exercises[0].max_weight == 60.0


def test_an_exercise_max_weight_beats_the_load_type(write_config):
    """Only this one movement is capped, and the rack is not."""
    text = FIXTURE.replace(
        "  weight_steps:",
        "  max_weights:\n    barbell: 100.0\n\n  weight_steps:",
    ).replace(
        "        load: barbell", "        load: barbell\n        max_weight: 60.0"
    )
    config = load_config(write_config(text))
    assert config["Workout A"].exercises[0].max_weight == 60.0


def test_no_max_weights_at_all_means_no_ceiling(write_config):
    """Unset is None rather than zero: zero would be a real maximum."""
    config = load_config(write_config(FIXTURE))
    assert config["Workout A"].exercises[0].max_weight is None


def test_a_negative_max_weight_is_rejected(write_config):
    text = FIXTURE.replace(
        "        load: barbell", "        load: barbell\n        max_weight: -5"
    )
    with pytest.raises(ConfigError, match="negative max_weight"):
        load_config(write_config(text))


def test_a_max_weight_below_the_min_weight_is_rejected(write_config):
    """Nothing could be prescribed between them, so neither end is honoured."""
    text = FIXTURE.replace(
        "        load: barbell",
        "        load: barbell\n        min_weight: 20.0\n        max_weight: 10.0",
    )
    with pytest.raises(ConfigError, match="below its min_weight"):
        load_config(write_config(text))


def test_a_start_weight_above_the_max_weight_is_rejected(write_config):
    """The one moment no session exists yet to correct it."""
    text = FIXTURE.replace(
        "        load: barbell",
        "        load: barbell\n        start_weight: 30.0\n        max_weight: 10.0",
    )
    with pytest.raises(ConfigError, match="above its own max_weight"):
        load_config(write_config(text))


# --- reading a stored weight as a real load --------------------------------


def test_the_load_reads_as_the_stored_weight_by_default(write_config):
    """Nothing is inferred, so every existing config keeps its meaning."""
    squat = load_config(write_config(FIXTURE))["Workout A"].exercises[0]
    assert squat.bodyweight_factor == 0.0


def test_bodyweight_factor_is_read_per_exercise(write_config):
    text = FIXTURE.replace(
        "        sets: 4\n", "        sets: 4\n        bodyweight_factor: 0.85\n"
    )
    squat = load_config(write_config(text))["Workout A"].exercises[0]
    assert squat.bodyweight_factor == 0.85


def test_a_bodyweight_factor_above_one_is_rejected(write_config):
    text = FIXTURE.replace(
        "        sets: 4\n", "        sets: 4\n        bodyweight_factor: 80\n"
    )
    with pytest.raises(ConfigError, match="between 0 and 1"):
        load_config(write_config(text))


def test_bodyweight_is_unset_so_that_garmin_is_asked(write_config):
    assert load_config(write_config(FIXTURE)).bodyweight is None


def test_bodyweight_can_be_stated_instead(write_config):
    text = FIXTURE.replace("settings:\n", "settings:\n  bodyweight: 81.5\n")
    assert load_config(write_config(text)).bodyweight == 81.5


# --- a cache pointed at a directory that moves ----------------------------


def caching_config(write_config, dump_dir, on=True):
    text = FIXTURE.replace(
        "settings:\n",
        f"settings:\n  garmin:\n    dump_dir: {dump_dir}\n"
        f"    activity_caching: {'true' if on else 'false'}\n",
        1,
    )
    return write_config(text)


def loading(path, caplog) -> str:
    with caplog.at_level(logging.WARNING, logger="repwise.config"):
        load_config(path)
    return caplog.text


def test_a_relative_dump_dir_with_caching_on_is_warned_about(write_config, caplog):
    """Each directory you run from would be its own empty cache."""
    warned = loading(caching_config(write_config, "."), caplog)

    assert "relative to wherever repwise is run from" in warned
    assert default_dump_dir() in warned, "which the setting can simply be dropped for"


def test_a_relative_dump_dir_is_still_honoured(write_config, caplog):
    """Warned, not refused: running from one directory is a coherent thing."""
    config = load_config(caching_config(write_config, "./dumps"))

    assert config.garmin.dump_dir == "./dumps"


def test_an_absolute_dump_dir_is_not_warned_about(write_config, caplog):
    warned = loading(caching_config(write_config, "/tmp/dumps"), caplog)

    assert warned == ""


def test_a_dump_dir_under_home_counts_as_absolute(write_config, caplog):
    """`~` is expanded as the config is read, so it names one directory."""
    warned = loading(caching_config(write_config, "~/dumps"), caplog)

    assert warned == ""


def test_a_relative_dump_dir_without_caching_is_left_alone(write_config, caplog):
    """Nothing is being read back, so it is somewhere to drop files and no more."""
    warned = loading(caching_config(write_config, ".", on=False), caplog)

    assert warned == ""


# --- a setting that wants a yes or a no -----------------------------------


def flagged(write_config, value):
    return write_config(
        FIXTURE.replace(
            "settings:\n",
            f"settings:\n  garmin:\n    activity_caching: {value}\n",
            1,
        )
    )


def test_a_quoted_false_is_refused_rather_than_read_as_true(write_config):
    """`bool("false")` is true, which would turn the cache on for a typo."""
    with pytest.raises(ConfigError, match="should be true or false"):
        load_config(flagged(write_config, '"false"'))


def test_a_number_is_refused_too(write_config):
    with pytest.raises(ConfigError, match="should be true or false"):
        load_config(flagged(write_config, "1"))


def test_a_key_written_with_nothing_after_it_means_the_default(write_config):
    """Not an answer, so not a `false` either - it is unfinished."""
    assert load_config(flagged(write_config, "")).garmin.activity_caching is False


def test_yaml_spells_yes_and_no_several_ways_and_all_of_them_work(write_config):
    """YAML 1.1 booleans: the loader resolves these, so the config need not."""
    for spelling in ("true", "True", "yes", "on"):
        config = load_config(flagged(write_config, spelling))
        assert config.garmin.activity_caching is True, spelling
