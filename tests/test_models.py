"""The domain objects, and what they derive from a routine.

Mostly this covers `ExerciseSpec.note`: the one-line summary of how an
exercise is programmed that gets written into the notes field of its Garmin
step, so the watch can show what you are working towards mid-set.
"""

from builders import spec

from repwise.domain.models import (
    STORED_NOTE,
    ExerciseSpec,
    LoadTier,
    Workout,
)


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


def test_a_cue_written_across_lines_is_joined_onto_one():
    """`GENERATED_NOTE` cannot match a note holding a newline, and a note this
    tool wrote that it cannot match is read as one typed into Connect: warned
    about, left alone, and never carrying a config change again."""
    written = spec(
        rep_low=6,
        rep_high=10,
        weight_step=2.5,
        notes="brace before you unrack\nknees out\n",
    ).note
    assert written == "6-10 reps | +2.5 kg | brace before you unrack knees out"


def test_a_note_is_cut_to_what_garmin_will_keep():
    """Garmin drops the rest without saying so, and what it stored would then
    never equal what we meant to write - so every run would find the note stale
    and write it again, for good."""
    written = spec(
        rep_low=6, rep_high=10, weight_step=2.5, notes="brace hard and " * 40
    ).note
    assert len(written) == STORED_NOTE


def test_no_cue_leaves_the_note_exactly_as_it_was():
    assert "|" in spec(rep_low=6, rep_high=10, weight_step=2.5, notes=None).note
    assert (
        spec(rep_low=6, rep_high=10, weight_step=2.5, notes=None).note.count("|") == 1
    )


# --- which rack a weight is on --------------------------------------------
#
# All of this was covered and none of it was asserted: mutation testing found
# `LoadTier.step`, both halves of `tier_for` and `Workout.claims` free to
# return something else with the suite still green. Three racks rather than
# two, because two cannot tell "the lightest rack above" from "the heaviest".

RACKS = (
    LoadTier(1.0, 10.0, (1.0,)),  # fixed dumbbells, 1-10 in ones
    LoadTier(12.0, 30.0, (2.0,)),  # the rack beside them, 12-30 in twos
    LoadTier(32.0, 50.0, (2.0,)),  # and the heavy one
)


def racked(**kwargs) -> ExerciseSpec:
    return spec(load="dumbbell", weight_step=1.0, tiers=RACKS, **kwargs)


def test_the_step_of_a_tier_is_its_smallest_increment():
    """A stack that takes 1.25 kg plates as well as 5 kg pin moves steps by
    1.25 when asked for one number. The largest is what `chosen_step` may work
    up to, never what the tier states."""
    assert LoadTier(0.0, None, (1.25, 5.0)).step == 1.25
    assert LoadTier(0.0, None, ()).step == 0.0


def test_a_weight_on_a_rack_finds_that_rack():
    assert racked().tier_for(6.0) == RACKS[0]
    assert racked().tier_for(20.0) == RACKS[1]
    assert racked().tier_for(40.0) == RACKS[2]


def test_a_weight_in_the_gap_goes_to_the_lightest_rack_above_it():
    """11 kg is on neither: the small rack ends at 10 and the next starts at
    12. It is answered with 12-30 because a load above a rack's ceiling got
    there by going up - and with the *lightest* rack above, not the heaviest,
    which is what a third rack is here to tell apart."""
    assert racked().tier_for(11.0) == RACKS[1]
    assert racked().tier_for(31.0) == RACKS[2]


def test_a_weight_exactly_at_a_rack_minimum_is_on_that_rack():
    """The boundary belongs to the rack that holds it, not to the gap below."""
    assert racked().tier_for(12.0) == RACKS[1]
    assert racked().tier_for(1.0) == RACKS[0]


def test_a_weight_below_every_rack_gets_the_lightest_one():
    """Nothing above it could ever hold it, so the lightest is the only
    honest answer."""
    assert racked().tier_for(0.5) == RACKS[0]


def test_a_weight_above_every_rack_gets_the_heaviest_one():
    assert racked().tier_for(80.0) == RACKS[2]


# --- which workout an activity belongs to ---------------------------------


def test_a_prefix_matches_only_at_the_start_of_the_name():
    """`claims` is what decides which workout a session advances. Matching
    anywhere in the name would let "Evening Training A" claim it, and a
    workout named after a substring of another would claim both."""
    workout = Workout("Workout A", "1", ["training a"], [])

    assert workout.claims("Training A - Push")
    assert not workout.claims("Evening Training A")
    assert not workout.claims("Re-Training A")


def test_a_prefix_is_matched_without_regard_to_case():
    workout = Workout("Workout A", "1", ["training a"], [])

    assert workout.claims("TRAINING A")


def test_a_workout_with_no_prefixes_claims_nothing():
    assert not Workout("Workout A", "1", [], []).claims("Training A")
