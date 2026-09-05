"""The domain objects, and what they derive from a routine.

Mostly this covers `ExerciseSpec.note`: the one-line summary of how an
exercise is programmed that gets written into the notes field of its Garmin
step, so the watch can show what you are working towards mid-set.
"""

from builders import spec


def test_note_states_the_range_and_the_weight_step():
    assert spec().note == "6-10 reps | +5 kg"


def test_note_uses_seconds_for_a_timed_hold():
    plank = spec(
        rep_low=30, rep_high=60, load="bodyweight", weight_step=0.0, unit="seconds"
    )
    assert plank.note == "30-60 s | bodyweight"


def test_note_says_bodyweight_where_there_is_no_load_to_add():
    situp = spec(rep_low=10, rep_high=25, load="bodyweight", weight_step=0.0)
    assert situp.note == "10-25 reps | bodyweight"


def test_note_names_the_rep_step_when_it_is_not_one():
    """A per-side exercise climbs two at a time, which the note has to say."""
    lunge = spec(rep_low=16, rep_high=24, rep_step=2, load="dumbbell", weight_step=1.0)
    assert lunge.note == "16-24 reps by 2 | +1 kg"


def test_note_trims_a_whole_number_weight_step():
    """1.0 reads as +1 kg, but 2.5 keeps its decimal."""
    assert spec(load="dumbbell", weight_step=1.0).note.endswith("| +1 kg")
    assert spec(load="cable", weight_step=2.5).note.endswith("| +2.5 kg")


def test_bodyweight_and_time_based_read_from_the_load_and_unit():
    assert spec(load="bodyweight", weight_step=0.0).bodyweight
    assert not spec().bodyweight
    assert spec(unit="seconds").time_based
    assert not spec().time_based


# --- the note a watch reads ------------------------------------------------


def test_a_note_says_the_range_and_the_step():
    written = spec(rep_low=6, rep_high=10, weight_step=2.5).note
    assert written == "6-10 reps | +2.5 kg"


def test_an_exercise_cue_is_written_on_the_end_of_it():
    """Which is the whole reason `notes` exists: it is read mid-set."""
    written = spec(
        rep_low=6, rep_high=10, weight_step=2.5, notes="2-3 RIR | brace, knees out"
    ).note
    assert written == "6-10 reps | +2.5 kg | 2-3 RIR | brace, knees out"


def test_no_cue_leaves_the_note_exactly_as_it_was():
    assert "|" in spec(rep_low=6, rep_high=10, weight_step=2.5, notes=None).note
    assert (
        spec(rep_low=6, rep_high=10, weight_step=2.5, notes=None).note.count("|") == 1
    )
