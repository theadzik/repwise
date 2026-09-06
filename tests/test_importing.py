"""What `import` selects, where it writes, and what it refuses to overwrite.

`importer.py` is what turns a payload into config text and is tested on its
own. This is the command around it: which workouts it asks Garmin for, whether
the result goes to stdout or to a file, and the one destructive thing it could
do - overwriting a workouts.yaml someone has already filled in.
"""

import logging
from typing import Any

import pytest
from builders import payload, rep_step, repeat

from repwise.app.importing import ImportOptions, run_import, select
from repwise.errors import ActivityNotFound, ExitCode, UsageError


class Account:
    """The workouts an account holds, and which were fetched in full."""

    def __init__(self, *summaries):
        self.summaries = list(summaries)
        self.fetched = []

    def list_workouts(self, sport_type=None):
        return self.summaries

    def workout(self, workout_id):
        self.fetched.append(workout_id)
        return payload(
            repeat(rep_step("BARBELL_BACK_SQUAT", "SQUAT", 8, 60000.0)),
            name=self.named(workout_id),
            workout_id=int(workout_id),
        )

    def named(self, workout_id):
        for each in self.summaries:
            if str(each["workoutId"]) == str(workout_id):
                return each["workoutName"]
        return "Unnamed"


def account(*summaries) -> Any:  # noqa: ANN401
    """`Any` rather than the class, so that a stand-in goes where a real
    session is asked for without a `cast` at every call site."""
    return Account(*summaries)


def summary(workout_id, name):
    return {"workoutId": workout_id, "workoutName": name}


PUSH = summary("123", "Push Day")
PULL = summary("456", "Pull Day")


# --- which workouts an import covers ---------------------------------------


def test_everything_is_imported_when_nothing_is_named():
    assert select(account(PUSH, PULL), ImportOptions()) == [PUSH, PULL]


def test_an_id_selects_exactly_one():
    assert select(account(PUSH, PULL), ImportOptions(id="456")) == [PULL]


@pytest.mark.parametrize("typed", ["push", "PUSH", "Push Day", "sh D"])
def test_a_name_is_matched_loosely_and_case_insensitively(typed):
    """You are reading the name off Connect, not retyping it exactly.

    Both sides are lowered, so the case of what was typed matters as little as
    the case Garmin happens to store.
    """
    assert select(account(PUSH, PULL), ImportOptions(name=typed)) == [PUSH]


def test_a_name_matching_several_imports_all_of_them():
    both = select(account(PUSH, PULL), ImportOptions(name="day"))

    assert both == [PUSH, PULL]


def test_an_id_that_is_not_there_says_so():
    with pytest.raises(ActivityNotFound, match="789"):
        select(account(PUSH), ImportOptions(id="789"))


def test_a_name_that_matches_nothing_lists_what_there_was():
    """The names are the fix, and looking them up is a second command."""
    with pytest.raises(ActivityNotFound, match="Push Day"):
        select(account(PUSH), ImportOptions(name="legs"))


# --- where the result goes -------------------------------------------------


def test_with_no_output_the_config_goes_to_stdout(capsys):
    """Config content, not a report: it has to stay redirectable."""
    assert run_import(account(PUSH), ImportOptions()) == ExitCode.OK

    printed = capsys.readouterr().out
    assert "BARBELL_BACK_SQUAT" in printed
    assert "workouts:" in printed


def test_stdout_carries_no_log_prefix(capsys, caplog):
    """A `repwise import > workouts.yaml` that picked one up would be broken."""
    with caplog.at_level(logging.INFO, logger="repwise.app.importing"):
        run_import(account(PUSH), ImportOptions())

    assert caplog.messages == []
    assert not capsys.readouterr().out.startswith("INFO")


def test_an_output_path_is_written_and_summarised(tmp_path, caplog):
    out = tmp_path / "workouts.yaml"

    with caplog.at_level(logging.INFO, logger="repwise.app.importing"):
        assert run_import(account(PUSH), ImportOptions(output=str(out))) == ExitCode.OK

    assert "BARBELL_BACK_SQUAT" in out.read_text()
    said = "\n".join(caplog.messages)
    assert "1 workout(s)" in said
    assert str(out) in said


def test_the_reader_is_told_the_result_needs_finishing(tmp_path, caplog):
    """An import cannot know a rep range or a step, so it leaves TODOs."""
    out = tmp_path / "workouts.yaml"

    with caplog.at_level(logging.INFO, logger="repwise.app.importing"):
        run_import(account(PUSH), ImportOptions(output=str(out)))

    assert "TODO" in "\n".join(caplog.messages)


def test_only_the_selected_workouts_are_fetched_in_full():
    """A summary has no steps, so each import costs one request - and only
    the ones being imported should."""
    fetching = account(PUSH, PULL)

    run_import(fetching, ImportOptions(id="123"))

    assert fetching.fetched == ["123"]


# --- the one thing it could destroy ----------------------------------------


def test_an_existing_file_is_never_overwritten_silently(tmp_path):
    out = tmp_path / "workouts.yaml"
    out.write_text("# a routine someone has already filled in\n")

    with pytest.raises(UsageError, match="--force"):
        run_import(account(PUSH), ImportOptions(output=str(out)))

    assert out.read_text().startswith("# a routine")


def test_force_overwrites_it(tmp_path):
    out = tmp_path / "workouts.yaml"
    out.write_text("# replaced\n")

    assert (
        run_import(account(PUSH), ImportOptions(output=str(out), force=True))
        == ExitCode.OK
    )
    assert "BARBELL_BACK_SQUAT" in out.read_text()


def test_nothing_is_written_when_the_selection_fails(tmp_path):
    """The refusal comes before the file is touched, so a mistyped name
    cannot cost you the config you were importing into."""
    out = tmp_path / "workouts.yaml"

    with pytest.raises(ActivityNotFound):
        run_import(account(PUSH), ImportOptions(name="legs", output=str(out)))

    assert not out.exists()
