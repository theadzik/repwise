"""The double progression rules."""

from conftest import held, spec

from workout.progression import PerformedSet as P
from workout.progression import Target, next_target, working_weight

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


# --- the four rules -------------------------------------------------------


def test_add_one_rep_when_target_met():
    target, why = next_target(SQUAT, Target(7, 20.0), [P(7, 20.0)] * 3)
    assert target == Target(8, 20.0), why


def test_repeat_when_any_set_misses():
    target, why = next_target(SQUAT, Target(8, 20.0), [P(8, 20.0), P(8, 20.0), P(6, 20.0)])
    assert target == Target(8, 20.0)
    assert "missed" in why


def test_exceeding_target_still_counts_as_met():
    target, _ = next_target(SQUAT, Target(7, 20.0), [P(9, 20.0), P(8, 20.0), P(7, 20.0)])
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


def test_dropping_weight_rebases_downward():
    """A deload is respected rather than treated as a failed session."""
    target, why = next_target(FOUR_SET_SQUAT, Target(9, 20.0), [P(8, 15.0)] * 4)
    assert target == Target(9, 15.0), why


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
