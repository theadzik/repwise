"""Reading a stored weight as the load it actually is."""

from builders import spec

from repwise.domain.effort import (
    TOLERATED_SHIFT,
    effective_load,
    fitting_rep_highs,
    reset_drop,
    worked_reps,
)

#: The exercise this module exists for: 12-20 reps, 5 kg step, and 80 kg of
#: lifter riding on top of a 20 kg stack.
CALF = spec(
    name="Weighted Standing Calf Raise",
    garmin_name="WEIGHTED_STANDING_CALF_RAISE",
    garmin_category="CALF_RAISE",
    rep_low=12,
    rep_high=20,
    sets=3,
    load="machine",
    weight_step=5.0,
    bodyweight_factor=1.0,
)

#: A pair of dumbbells, entered as the weight of the pair and stepping by the
#: pair, which is what makes any notion of "how many implements" unnecessary.
LUNGE = spec(
    name="Alternating Dumbbell Lunge",
    garmin_name="ALTERNATING_DUMBBELL_LUNGE",
    rep_low=16,
    rep_high=24,
    rep_step=2,
    sets=4,
    load="dumbbell",
    weight_step=2.0,
    bodyweight_factor=0.85,
)


def test_a_plain_exercise_is_its_stored_weight():
    """The default has to be a no-op, or every barbell lift changes meaning."""
    pulldown = spec(load="cable", weight_step=5.0)
    assert effective_load(pulldown, 45.0, bodyweight=80.0) == 45.0


def test_bodyweight_is_added_at_its_factor():
    assert effective_load(CALF, 20.0, bodyweight=80.0) == 100.0


def test_bodyweight_is_added_on_top_of_a_pair_entered_whole():
    # 24 kg of dumbbell as entered, plus 0.85 of an 80 kg lifter.
    assert effective_load(LUNGE, 24.0, bodyweight=80.0) == 24.0 + 68.0


def test_the_calf_raise_weight_jump_is_a_large_step_backwards():
    """20 reps at 100 kg -> 12 reps at 105 kg, which is easier, not harder."""
    drop = reset_drop(CALF, 20.0, bodyweight=80.0)
    assert drop is not None
    assert 0.11 < drop < 0.13
    assert drop > TOLERATED_SHIFT


def test_ignoring_bodyweight_hides_the_problem_entirely():
    """Why the field has to exist: on the stack alone the range looks fine."""
    blind = reset_drop(CALF, 20.0, bodyweight=0.0)
    assert blind is not None
    assert blind < 0  # 5 kg on 20 kg is a real 25% jump, and pays for itself


def test_a_narrower_range_pays_for_its_own_reset():
    narrowed = spec(**{**CALF.__dict__, "rep_low": 15, "rep_high": 18})
    drop = reset_drop(narrowed, 20.0, bodyweight=80.0)
    assert drop is not None
    assert abs(drop) < 0.02


def test_an_ordinary_barbell_lift_stays_under_the_tolerance():
    """The sawtooth is inherent, so a sane range must not be reported."""
    squat = spec(rep_low=6, rep_high=10, load="barbell", weight_step=2.5)
    drop = reset_drop(squat, 60.0)
    assert drop is not None
    assert 0 < drop <= TOLERATED_SHIFT


def test_bodyweight_and_timed_exercises_have_no_weight_jump_to_judge():
    assert reset_drop(spec(load="bodyweight", weight_step=0.0), 0.0) is None
    assert reset_drop(spec(unit="seconds"), 30.0) is None


def test_an_unloaded_exercise_is_not_divided_by_zero():
    assert reset_drop(CALF, 0.0, bodyweight=0.0) is None


def test_the_suggested_range_is_one_the_step_can_pay_for():
    fitted = fitting_rep_highs(CALF, 20.0, bodyweight=80.0)
    assert fitted is not None
    assert CALF.rep_low < fitted.balanced < CALF.rep_high

    narrowed = spec(**{**CALF.__dict__, "rep_high": fitted.balanced})
    drop = reset_drop(narrowed, 20.0, bodyweight=80.0)
    assert drop is not None and drop <= TOLERATED_SHIFT


def test_the_balanced_top_is_the_one_nearest_to_breaking_even():
    """What makes it the suggestion rather than merely a member of the window:
    every other top inside the tolerance costs more than it does."""
    fitted = fitting_rep_highs(CALF, 20.0, bodyweight=80.0)
    assert fitted is not None

    def cost(rep_high):
        drop = reset_drop(spec(**{**CALF.__dict__, "rep_high": rep_high}), 20.0, 80.0)
        assert drop is not None
        return abs(drop)

    others = range(fitted.narrowest, fitted.widest + 1)
    assert cost(fitted.balanced) == min(cost(each) for each in others)


def test_the_window_holds_every_top_that_fits_and_nothing_else():
    """Reported as two ends, which only describes the window because
    `reset_drop` is monotonic in `rep_high` and so the fits are contiguous."""
    fitted = fitting_rep_highs(CALF, 20.0, bodyweight=80.0)
    assert fitted is not None
    assert fitted.narrowest <= fitted.balanced <= fitted.widest

    def fits(rep_high):
        drop = reset_drop(spec(**{**CALF.__dict__, "rep_high": rep_high}), 20.0, 80.0)
        return drop is not None and abs(drop) <= TOLERATED_SHIFT

    assert all(fits(each) for each in range(fitted.narrowest, fitted.widest + 1))
    assert not fits(fitted.widest + 1)
    # Nothing below the window fits either, except where the window already
    # starts at the first rung above `rep_low` and there is no room below it.
    assert fitted.narrowest == CALF.rep_low + CALF.rep_step or not fits(
        fitted.narrowest - CALF.rep_step
    )


def test_the_bottom_of_the_range_is_never_part_of_the_answer():
    """`rep_low` says how heavy the exercise is allowed to get, which is a
    decision rather than an arithmetic result. Only the top is offered."""
    fitted = fitting_rep_highs(CALF, 20.0, bodyweight=80.0)
    assert fitted is not None
    assert min(fitted.narrowest, fitted.balanced) > CALF.rep_low


def test_even_a_trivial_step_can_afford_some_range():
    """Narrowing helps whatever the step is, because the reset is the cost."""
    trivial = spec(**{**CALF.__dict__, "weight_step": 0.1})
    fitted = fitting_rep_highs(trivial, 20.0, bodyweight=80.0)
    assert fitted is not None and fitted.balanced < CALF.rep_high


def test_no_range_is_suggested_when_nothing_meets_the_tolerance():
    """Reachable only by demanding a reset that costs literally nothing."""
    trivial = spec(**{**CALF.__dict__, "weight_step": 0.1})
    assert fitting_rep_highs(trivial, 20.0, bodyweight=80.0, tolerance=0.0) is None


# --- exercises the watch counts per side -----------------------------------


def test_alternating_reps_are_halved_before_epley_sees_them():
    """A 16-24 range is 8-12 for the leg doing the work."""
    assert worked_reps(LUNGE, 24) == 12
    assert worked_reps(LUNGE, 16) == 8


def test_an_ordinary_exercise_works_the_reps_it_is_programmed():
    assert worked_reps(CALF, 20) == 20


def test_counting_both_legs_would_invent_a_finding():
    """The bug this exists to stop: 5 kg dumbbells, a range that is fine."""
    drop = reset_drop(LUNGE, 10.0, bodyweight=81.0)
    assert drop is not None
    assert drop < TOLERATED_SHIFT

    # Read in the watch's units instead, the same exercise looks broken.
    doubled = spec(**{**LUNGE.__dict__, "rep_step": 1})
    naive = reset_drop(doubled, 10.0, bodyweight=81.0)
    assert naive is not None and naive > TOLERATED_SHIFT


def test_a_suggested_range_lands_on_a_rung_the_ladder_reaches():
    """Narrowing 16-24 to 21 would straddle the top and never earn the jump."""
    wide = spec(**{**LUNGE.__dict__, "rep_low": 10, "rep_high": 30})
    fitted = fitting_rep_highs(wide, 10.0, bodyweight=81.0)

    assert fitted is not None
    for top in (fitted.narrowest, fitted.balanced, fitted.widest):
        assert (top - wide.rep_low) % wide.rep_step == 0


# --- ranges too narrow for their step --------------------------------------


#: A 1 kg step on a 3 kg dumbbell is a 33% jump, which a 12-20 range cannot
#: absorb: the "reset" to 12 reps is harder than the 20 that earned it.
RAISE = spec(
    name="Dumbbell Lateral Raise",
    garmin_name="DUMBBELL_LATERAL_RAISE",
    rep_low=12,
    rep_high=20,
    sets=3,
    load="dumbbell",
    weight_step=1.0,
)


def test_a_step_too_big_for_its_range_reads_as_a_negative_shift():
    shift = reset_drop(RAISE, 3.0)
    assert shift is not None
    assert shift < -TOLERATED_SHIFT


def test_the_same_exercise_is_fine_once_the_dumbbells_are_heavier():
    """The wall is the step as a share of the load, not the range alone."""
    shift = reset_drop(RAISE, 12.0)
    assert shift is not None
    assert abs(shift) <= TOLERATED_SHIFT


def test_a_narrow_range_is_fixed_by_widening_it():
    fitted = fitting_rep_highs(RAISE, 3.0)
    assert fitted is not None
    assert fitted.balanced > RAISE.rep_high  # widened, not narrowed

    widened = spec(**{**RAISE.__dict__, "rep_high": fitted.balanced})
    shift = reset_drop(widened, 3.0)
    assert shift is not None and abs(shift) <= TOLERATED_SHIFT


def test_a_wide_range_is_still_fixed_by_narrowing_it():
    """Both directions from one search, decided by the sign alone."""
    fitted = fitting_rep_highs(CALF, 20.0, bodyweight=80.0)
    assert fitted is not None
    assert fitted.balanced < CALF.rep_high


def test_micro_loading_fixes_what_no_range_can():
    """A 1 kg step on 1 kg is a 100% jump; no rep range absorbs that."""
    assert fitting_rep_highs(RAISE, 1.0) is None
