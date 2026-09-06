"""Reading and writing workouts.yaml, and what happens when either fails.

The one file this tool owns on the user's disk. `read` turns anything that
goes wrong into a `ConfigError`, because a traceback out of PyYAML says
nothing a person can act on; `write` puts the new text beside the destination
and moves it on, so a run that dies half way leaves the config exactly as it
was rather than in pieces.
"""

import os

import pytest

from repwise.errors import ConfigError
from repwise.yamlio import dump, read, write

# --- reading ---------------------------------------------------------------


def test_a_document_is_parsed(tmp_path):
    path = tmp_path / "workouts.yaml"
    path.write_text("workouts:\n  - key: Workout A\n")

    assert read(str(path)) == {"workouts": [{"key": "Workout A"}]}


def test_a_file_that_is_not_there_is_a_config_error(tmp_path):
    """Not a FileNotFoundError traceback: the path is something the user
    chose, so it is a configuration problem like any other."""
    with pytest.raises(ConfigError, match="could not be read"):
        read(str(tmp_path / "absent.yaml"))


def test_a_directory_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError, match="could not be read"):
        read(str(tmp_path))


def test_malformed_yaml_names_the_file_rather_than_raising_from_yaml(tmp_path):
    path = tmp_path / "workouts.yaml"
    path.write_text("workouts:\n  - key: [unclosed\n")

    with pytest.raises(ConfigError, match="is not valid YAML"):
        read(str(path))


# --- writing ---------------------------------------------------------------


def test_the_text_is_written(tmp_path):
    path = tmp_path / "workouts.yaml"

    write(str(path), "workouts: []\n")

    assert path.read_text() == "workouts: []\n"


def test_writing_replaces_rather_than_truncating(tmp_path):
    """The whole reason for the temporary file: the destination is never open
    for writing, so there is no window in which it holds half a config."""
    path = tmp_path / "workouts.yaml"
    path.write_text("workouts: [old]\n")

    write(str(path), "workouts: [new]\n")

    assert path.read_text() == "workouts: [new]\n"


def test_the_permissions_of_an_existing_file_are_kept(tmp_path):
    path = tmp_path / "workouts.yaml"
    path.write_text("workouts: []\n")
    os.chmod(path, 0o600)

    write(str(path), "workouts: [new]\n")

    assert oct(path.stat().st_mode)[-3:] == "600"


def test_a_failed_move_leaves_nothing_behind(tmp_path, monkeypatch):
    """The temporary sits in the destination's own directory, so one left
    there would be read back as a stray config by anyone looking."""
    path = tmp_path / "workouts.yaml"
    path.write_text("workouts: [old]\n")

    def refuse(*args, **kwargs):
        raise OSError("No space left on device")

    monkeypatch.setattr(os, "replace", refuse)

    with pytest.raises(ConfigError, match="could not be written"):
        write(str(path), "workouts: [new]\n")

    assert path.read_text() == "workouts: [old]\n", "not a byte of it lost"
    assert list(tmp_path.iterdir()) == [path], "the temporary was cleaned up"


def test_a_write_to_an_unwritable_directory_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError, match="could not be written"):
        write(str(tmp_path / "absent" / "workouts.yaml"), "workouts: []\n")


# --- dumping ---------------------------------------------------------------


def test_a_dump_round_trips_through_read(tmp_path):
    original = {"workouts": [{"key": "Workout A", "sets": 3, "load": "barbell"}]}
    path = tmp_path / "workouts.yaml"

    write(str(path), dump(original))

    assert read(str(path)) == original


def test_a_dump_keeps_the_order_it_was_given(tmp_path):
    """Not sorted: the file is read by a person, and the order the keys were
    written in is the order they make sense in."""
    written = dump({"key": "Workout A", "activity_prefixes": ["a"], "exercises": []})

    assert written.index("key:") < written.index("activity_prefixes:")
    assert written.index("activity_prefixes:") < written.index("exercises:")


def test_a_dump_keeps_non_ascii_readable(tmp_path):
    """`allow_unicode`, so a Polish workout name stays a Polish workout name
    rather than becoming an escape sequence nobody can search for."""
    assert "Trening Ćwiczenia" in dump({"key": "Trening Ćwiczenia"})
