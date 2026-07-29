"""The double progression rules."""

from builders import held, spec

from workout.domain.progression import PerformedSet as P
from workout.domain.progression import Target, next_target, working_weight

SQUAT = spec()
LUNGE = spec(
    name="Alternating Dumbbell Lunge",
    garmin_name="ALTERNATING_DUMBBELL_LUNGE",
    garmin_category="LUNGE",
    rep_low=8,
    rep_high=12,
    sets=5,
    load="dumbbell",
    weight_step=1.0,
)
PLANK = spec(
    name="Plank",
    garmin_name="PLANK",
    garmin_category="PLANK",
    rep_low=30,
    rep_high=60,
    load="bodyweight",
    weight_step=0.0,
    unit="seconds",
)
FOUR_SET_SQUAT = spec(sets=4, weight_step=2.5)
#: A narrow range on top of a load whose smallest step is a third of the
#: working weight, which is where an unearned jump lands furthest out of range.
LATERAL_RAISE = spec(
    name="Dumbbell Lateral Raise",
    garmin_name="DUMBBELL_LATERAL_RAISE",
    garmin_category="LATERAL_RAISE",
    rep_low=12,
    rep_high=15,
    load="dumbbell",
    weight_step=1.0,
)


# --- the four rules -------------------------------------------------------


def test_add_one_rep_when_target_met():
    target, why = next_target(SQUAT, Target(7, 20.0), [P(7, 20.0)] * 3)
    assert target == Target(8, 20.0), why


def test_repeat_when_any_set_misses():
    target, why = next_target(
        SQUAT, Target(8, 20.0), [P(8, 20.0), P(8, 20.0), P(6, 20.0)]
    )
    assert target == Target(8, 20.0)
    assert "missed" in why


def test_exceeding_target_still_counts_as_met():
    target, _ = next_target(
        SQUAT, Target(7, 20.0), [P(9, 20.0), P(8, 20.0), P(7, 20.0)]
    )
    assert target == Target(8, 20.0)


def test_top_of_range_adds_weight_and_resets():
    target, why = next_target(SQUAT, Target(10, 20.0), [P(10, 20.0)] * 3)
    assert target == Target(6, 25.0), why


def test_dumbbell_uses_its_own_step():
    target, _ = next_target(LUNGE, Target(12, 4.0), [P(12, 4.0)] * 5)
    assert target == Target(8, 5.0)


def test_missing_sets_counts_as_failure():
    """Only two sets logged when five were prescribed."""
    target, why = next_target(LUNGE, Target(9, 4.0), [P(9, 4.0), P(9, 4.0)])
    assert target == Target(9, 4.0)
    assert "2/5 sets" in why


def test_bodyweight_does_not_add_weight():
    target, why = next_target(PLANK, Target(60, 0.0), [P(60, 0.0)] * 3)
    assert target.weight == 0.0
    assert "bodyweight" in why


def test_no_sets_logged_is_a_noop():
    target, why = next_target(SQUAT, Target(8, 20.0), [])
    assert target == Target(8, 20.0)
    assert "no sets" in why


# --- progressing from the weakest set -------------------------------------


def test_overshoot_on_some_sets_does_not_accelerate():
    """Extra reps on the easy sets earn nothing while the weakest set lags."""
    target, _ = next_target(
        SQUAT, Target(7, 20.0), [P(7, 20.0), P(7, 20.0), P(10, 20.0), P(10, 20.0)]
    )
    assert target == Target(8, 20.0)


def test_overshoot_on_every_set_advances_by_what_was_done():
    target, why = next_target(SQUAT, Target(7, 20.0), [P(8, 20.0)] * 4)
    assert target == Target(9, 20.0), why
    assert "beat target (8 on every set vs 7)" in why


def test_matching_the_target_exactly_does_not_mention_beating_it():
    _, why = next_target(SQUAT, Target(7, 20.0), [P(7, 20.0)] * 4)
    assert "beat target" not in why


def test_reaching_top_of_range_adds_weight_even_if_target_was_lower():
    """Performing 10s on every set tops out the range, whatever was asked."""
    target, why = next_target(SQUAT, Target(7, 20.0), [P(10, 20.0)] * 4)
    assert target == Target(6, 25.0), why


def test_overshoot_is_capped_at_top_of_range():
    """A set above rep_high still resets rather than targeting 11+."""
    target, _ = next_target(
        SQUAT, Target(9, 20.0), [P(12, 20.0), P(11, 20.0), P(10, 20.0), P(10, 20.0)]
    )
    assert target == Target(6, 25.0)


def test_bodyweight_overshoot_targets_top_of_range():
    target, why = next_target(PLANK, Target(45, 0.0), [P(60, 0.0)] * 3)
    assert target == Target(60, 0.0), why
    assert "bodyweight" in why


# --- mid-session weight changes -------------------------------------------


def test_working_weight_is_the_modal_load():
    assert working_weight([P(10, 20.0), P(8, 22.5), P(8, 22.5), P(8, 22.5)]) == 22.5


def test_working_weight_breaks_ties_heavier():
    assert working_weight([P(8, 20.0), P(8, 20.0), P(8, 22.5), P(8, 22.5)]) == 22.5


def test_weight_bumped_mid_session_is_banked_not_discarded():
    """Max reps on set 1, then up to 22.5 kg for the rest: keep the 22.5."""
    target, why = next_target(
        FOUR_SET_SQUAT,
        Target(7, 20.0),
        [P(10, 20.0), P(8, 22.5), P(8, 22.5), P(8, 22.5)],
    )
    assert target == Target(8, 22.5), why


def test_full_sets_at_new_weight_progress_from_there():
    target, why = next_target(FOUR_SET_SQUAT, Target(7, 20.0), [P(8, 22.5)] * 4)
    assert target == Target(9, 22.5), why


def test_new_weight_at_top_of_range_still_adds_load():
    target, why = next_target(FOUR_SET_SQUAT, Target(7, 20.0), [P(10, 22.5)] * 4)
    assert target == Target(6, 25.0), why


def test_bottom_of_range_to_top_at_a_heavier_load_adds_load_again():
    """Planned 6 x 20, did 10 x 22.5: the session's load is the new baseline."""
    target, why = next_target(FOUR_SET_SQUAT, Target(6, 20.0), [P(10, 22.5)] * 4)
    assert target == Target(6, 25.0), why
    assert why == (
        "hit 10 on every set at 22.5 kg (planned 20 kg), +2.5 kg and reset to 6"
    )


def test_top_of_range_at_a_heavier_load_adds_load_from_what_was_lifted():
    """Planned 10 x 20, did 10 x 22.5: step up from 22.5, not from 20."""
    target, why = next_target(FOUR_SET_SQUAT, Target(10, 20.0), [P(10, 22.5)] * 4)
    assert target == Target(6, 25.0), why
    assert why == (
        "hit 10 on every set at 22.5 kg (planned 20 kg), +2.5 kg and reset to 6"
    )


def test_top_of_range_at_the_planned_load_does_not_mention_the_load():
    _, why = next_target(FOUR_SET_SQUAT, Target(10, 20.0), [P(10, 20.0)] * 4)
    assert why == "hit 10 on every set, +2.5 kg and reset to 6"


def test_dropping_weight_rebases_downward():
    """A deload is respected rather than treated as a failed session."""
    target, why = next_target(FOUR_SET_SQUAT, Target(9, 20.0), [P(8, 15.0)] * 4)
    assert target == Target(9, 15.0), why


# --- a load below the range is not adopted --------------------------------


def test_heavier_load_below_range_keeps_the_previous_target():
    """The 3 kg pair was taken, so 4 kg x 8 - short of a 12-15 range."""
    target, why = next_target(LATERAL_RAISE, Target(13, 3.0), [P(8, 4.0)] * 3)
    assert target == Target(13, 3.0), why
    assert why == "only 8 at 4 kg, below the 12-15 range, keep 13 x 3 kg"


def test_heavier_load_at_the_bottom_of_the_range_is_adopted():
    """One rep more and the load is earned, so it becomes the new baseline."""
    target, why = next_target(LATERAL_RAISE, Target(13, 3.0), [P(12, 4.0)] * 3)
    assert target == Target(13, 4.0), why


def test_below_range_is_judged_by_the_weakest_set():
    """Two sets in range do not carry a third that fell out of it."""
    target, _ = next_target(
        LATERAL_RAISE, Target(13, 3.0), [P(13, 4.0), P(12, 4.0), P(9, 4.0)]
    )
    assert target == Target(13, 3.0)


def test_partial_sets_below_range_do_not_bank_the_weight():
    """Short of both the set count and the range: the load is still unearned."""
    target, why = next_target(LATERAL_RAISE, Target(13, 3.0), [P(8, 4.0)] * 2)
    assert target == Target(13, 3.0), why
    assert "below the 12-15 range" in why, "not the consolidate message"


def test_partial_sets_within_range_are_still_banked():
    """Rule 5 does not swallow the consolidate case when the reps are fine."""
    target, why = next_target(LATERAL_RAISE, Target(13, 3.0), [P(12, 4.0)] * 2)
    assert target == Target(12, 4.0), why
    assert "2/3 sets" in why


def test_dropping_below_the_range_is_not_adopted_either():
    """A deload only rebases while it still lands inside the range."""
    target, why = next_target(LATERAL_RAISE, Target(13, 3.0), [P(8, 2.0)] * 3)
    assert target == Target(13, 3.0), why


def test_same_load_below_the_range_still_reads_as_a_missed_target():
    """Rule 4 owns the unchanged-load case, so its wording is unaffected."""
    target, why = next_target(LATERAL_RAISE, Target(13, 3.0), [P(8, 3.0)] * 3)
    assert target == Target(13, 3.0)
    assert "missed target (8/13 on worst set), repeat" in why


def test_heavier_load_can_still_top_out_the_range():
    """Rule 5 gates rule 3 rather than replacing it."""
    target, why = next_target(LATERAL_RAISE, Target(13, 3.0), [P(15, 4.0)] * 3)
    assert target == Target(12, 5.0), why


# --- timed holds ----------------------------------------------------------


def test_timed_hold_adds_one_second_when_target_met():
    target, why = next_target(PLANK, Target(47, 0.0), held(47, 47, 47))
    assert target == Target(48, 0.0), why


def test_timed_hold_repeats_when_short():
    """46 s against a 47 s target is a miss, not a pass."""
    target, why = next_target(PLANK, Target(47, 0.0), held(46, 46, 46))
    assert target == Target(47, 0.0)
    assert "missed" in why


def test_timed_hold_rounds_fractional_durations():
    target, _ = next_target(PLANK, Target(47, 0.0), held(47.4, 47.6, 48.0))
    assert target == Target(48, 0.0)


def test_timed_hold_stops_at_top_of_range():
    target, why = next_target(PLANK, Target(60, 0.0), held(60, 60, 60))
    assert target == Target(60, 0.0), why


# --- rep_step -------------------------------------------------------------

LUNGE_DOUBLED = spec(
    name="Alternating Dumbbell Lunge",
    garmin_name="ALTERNATING_DUMBBELL_LUNGE",
    garmin_category="LUNGE",
    rep_low=16,
    rep_high=24,
    sets=4,
    load="dumbbell",
    weight_step=1.0,
    rep_step=2,
)


def test_rep_step_advances_by_two():
    target, why = next_target(LUNGE_DOUBLED, Target(22, 4.0), [P(22, 4.0)] * 4)
    assert target == Target(24, 4.0), why
    assert "add 2 reps" in why


def test_rep_step_does_not_overshoot_the_range():
    """From an odd 23 a +2 step would land on 25, past rep_high."""
    target, _ = next_target(LUNGE_DOUBLED, Target(23, 4.0), [P(23, 4.0)] * 4)
    assert target == Target(24, 4.0)


def test_rep_step_still_earns_the_weight_jump_at_the_top():
    target, why = next_target(LUNGE_DOUBLED, Target(24, 4.0), [P(24, 4.0)] * 4)
    assert target == Target(16, 5.0), why


def test_rep_step_defaults_to_one():
    assert SQUAT.rep_step == 1
    _, why = next_target(SQUAT, Target(7, 20.0), [P(7, 20.0)] * 3)
    assert "add 1 rep " in why, "singular wording for the default step"
