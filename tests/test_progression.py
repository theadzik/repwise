"""The double progression rules."""

from builders import held, spec

from repwise.domain.models import LoadTier
from repwise.domain.progression import PerformedSet as P
from repwise.domain.progression import (
    Session,
    Target,
    hit,
    miss_streak,
    next_target,
    working_weight,
)

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
    assert "2 of 5 sets" in why


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
    assert "beat target (8 on every set)" in why


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
    assert why == "hit 10 on every set at 22.5 kg, top of the range"


def test_top_of_range_at_a_heavier_load_adds_load_from_what_was_lifted():
    """Planned 10 x 20, did 10 x 22.5: step up from 22.5, not from 20."""
    target, why = next_target(FOUR_SET_SQUAT, Target(10, 20.0), [P(10, 22.5)] * 4)
    assert target == Target(6, 25.0), why
    assert why == "hit 10 on every set at 22.5 kg, top of the range"


def test_top_of_range_at_the_planned_load_does_not_mention_the_load():
    _, why = next_target(FOUR_SET_SQUAT, Target(10, 20.0), [P(10, 20.0)] * 4)
    assert why == "hit 10 on every set, top of the range"


def test_dropping_weight_rebases_downward():
    """A deload is respected rather than treated as a failed session."""
    target, why = next_target(FOUR_SET_SQUAT, Target(9, 20.0), [P(8, 15.0)] * 4)
    assert target == Target(9, 15.0), why


# --- a load below the range is not adopted --------------------------------


def test_heavier_load_below_range_keeps_the_previous_target():
    """The 3 kg pair was taken, so 4 kg x 8 - short of a 12-15 range."""
    target, why = next_target(LATERAL_RAISE, Target(13, 3.0), [P(8, 4.0)] * 3)
    assert target == Target(13, 3.0), why
    assert why == "only 8 at 4 kg, below the 12-15 range"


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
    assert "2 of 3 sets" in why


def test_dropping_below_the_range_is_not_adopted_either():
    """A deload only rebases while it still lands inside the range."""
    target, why = next_target(LATERAL_RAISE, Target(13, 3.0), [P(8, 2.0)] * 3)
    assert target == Target(13, 3.0), why


def test_same_load_below_the_range_still_reads_as_a_missed_target():
    """Rule 4 owns the unchanged-load case, so its wording is unaffected."""
    target, why = next_target(LATERAL_RAISE, Target(13, 3.0), [P(8, 3.0)] * 3)
    assert target == Target(13, 3.0)
    assert "missed target, 8 on the worst set" in why


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
    assert "add 1 rep" in why, "singular wording for the default step"


# --- granular progression -------------------------------------------------
#
# A hit advances by `sets - miss_streak` units, one unit being a rep_step on a
# single set, filled from the first set down. A clean session earns one per
# set, which is the whole target moving; a stall behind it buys fewer, so the
# way back up is gentler than the way that failed.


def test_a_clean_hit_still_moves_every_set():
    """The streak-free case is what this tool always did, and is unchanged."""
    target, why = next_target(FOUR_SET_SQUAT, Target(8, 20.0), [P(8, 20.0)] * 4)
    assert target == Target(9, 20.0), why


def test_a_hit_after_two_misses_moves_only_two_sets():
    target, why = next_target(
        FOUR_SET_SQUAT, Target(8, 20.0), [P(8, 20.0)] * 4, streak=2
    )
    assert target == Target(8, 20.0, lead=2), why
    assert target.spread(4) == "8+2"
    assert "hit after 2 misses" in why
    assert "add 1 rep on 2 of 4 sets" in why


def test_a_hit_after_one_miss_moves_all_but_one():
    target, _ = next_target(FOUR_SET_SQUAT, Target(8, 20.0), [P(8, 20.0)] * 4, streak=1)
    assert target.spread(4) == "8+3"


def test_a_hit_always_earns_at_least_one_set():
    """However long the stall, or it could never end."""
    target, why = next_target(
        FOUR_SET_SQUAT, Target(8, 20.0), [P(8, 20.0)] * 4, streak=9
    )
    assert target.spread(4) == "8+1", why


def test_a_ramped_target_levels_up_before_the_base_moves():
    """9,9,8,8 hit cleanly becomes 9,9,9,9 rather than 10,10,9,9."""
    ramped = Target(8, 20.0, lead=2)
    target, why = next_target(
        FOUR_SET_SQUAT, ramped, [P(9, 20.0)] * 2 + [P(8, 20.0)] * 2
    )
    assert target == Target(9, 20.0), why
    assert target.per_set(4) == [9, 9, 9, 9], "flat again, not 10,10,9,9"


def test_levelling_up_is_capped_by_what_the_ramp_has_left():
    """A clean session earns 4 units but only 1 is needed to finish 9,9,9,8."""
    target, _ = next_target(
        FOUR_SET_SQUAT,
        Target(8, 20.0, lead=3),
        [P(9, 20.0)] * 3 + [P(8, 20.0)],
    )
    assert target == Target(9, 20.0)


def test_a_ramp_can_widen_a_step_at_a_time_while_stalling():
    target, _ = next_target(
        FOUR_SET_SQUAT,
        Target(8, 20.0, lead=2),
        [P(9, 20.0)] * 2 + [P(8, 20.0)] * 2,
        streak=3,
    )
    assert target.spread(4) == "8+3"


def test_missing_the_high_sets_of_a_ramp_is_a_miss():
    """8,8,8,8 clears the base but not the two nines that were asked for."""
    ramped = Target(8, 20.0, lead=2)
    target, why = next_target(FOUR_SET_SQUAT, ramped, [P(8, 20.0)] * 4)
    assert target == ramped
    assert "missed target, 8 on the worst set" in why


def test_beating_a_ramp_everywhere_levels_it_and_advances_from_there():
    """10 on every set tops the range, ramp or no ramp."""
    target, why = next_target(
        FOUR_SET_SQUAT, Target(8, 20.0, lead=2), [P(10, 20.0)] * 4
    )
    assert target == Target(6, 22.5), why


def test_a_ramp_does_not_survive_a_change_of_load():
    """The ramp belonged to the old ladder, so the new load starts flat."""
    target, why = next_target(FOUR_SET_SQUAT, Target(8, 20.0, lead=2), [P(8, 22.5)] * 4)
    assert target == Target(9, 22.5), why


def test_a_ramp_does_not_survive_a_short_session():
    target, why = next_target(
        FOUR_SET_SQUAT, Target(8, 20.0, lead=2), [P(9, 20.0), P(9, 20.0)]
    )
    assert target.lead == 0, why


def test_the_streak_cannot_push_a_target_past_the_top_of_the_range():
    """At the top it is still a weight jump, whatever the streak was."""
    target, why = next_target(
        FOUR_SET_SQUAT, Target(10, 20.0), [P(10, 20.0)] * 4, streak=3
    )
    assert target == Target(6, 22.5), why


def test_a_ramp_steps_by_rep_step():
    """Per-side counting ramps 18,18,16,16 rather than 17,17,16,16."""
    target, _ = next_target(LUNGE_DOUBLED, Target(16, 4.0), [P(16, 4.0)] * 4, streak=2)
    assert target == Target(16, 4.0, lead=2)
    assert target.spread(4, rep_step=2) == "16+2"


def test_a_timed_hold_ramps_in_seconds():
    target, _ = next_target(PLANK, Target(47, 0.0), held(47, 47, 47), streak=1)
    assert target.spread(3) == "47+2"


def test_a_timed_hold_says_what_it_adds_in_seconds():
    """The step is a second, not a rep: a hold has no reps to add one to."""
    _, why = next_target(PLANK, Target(47, 0.0), held(47, 47, 47))
    assert "add 1 second" in why, why


def test_a_timed_hold_names_seconds_on_a_partial_advance():
    _, why = next_target(PLANK, Target(47, 0.0), held(47, 47, 47), streak=1)
    assert "add 1 second on 2 of 3 sets" in why, why


# --- partial progression turned off ---------------------------------------
#
# settings.partial_progression: false. The streak stops buying a smaller
# advance, and every set moves together in both directions: a hit adds a whole
# rep to the target, and an ease takes one off it.

WHOLE_SET_SQUAT = spec(sets=4, weight_step=2.5, partial_progression=False)


def test_a_hit_after_a_stall_still_moves_the_whole_target():
    target, why = next_target(
        WHOLE_SET_SQUAT, Target(8, 20.0), [P(8, 20.0)] * 4, streak=2
    )
    assert target == Target(9, 20.0), why
    assert "add 1 rep" in why
    assert "of 4 sets" not in why, "nothing was held back"


def test_a_hit_after_a_long_stall_moves_the_whole_target_too():
    """However long the streak: there is no smaller step to take."""
    target, why = next_target(
        WHOLE_SET_SQUAT, Target(8, 20.0), [P(8, 20.0)] * 4, streak=9
    )
    assert target == Target(9, 20.0), why


def test_a_ramp_left_behind_is_spent_by_the_next_hit():
    """A target ramped before the setting changed levels up and stays flat."""
    target, why = next_target(
        WHOLE_SET_SQUAT, Target(8, 20.0, lead=2), [P(9, 20.0)] * 2 + [P(8, 20.0)] * 2
    )
    assert target == Target(9, 20.0), why


def test_easing_takes_a_whole_rep_off_rather_than_one_set():
    """9,9,9,8 against a target of 9 eases to 8 everywhere, not to `8+3`."""
    target, why = next_target(
        WHOLE_SET_SQUAT,
        Target(9, 20.0),
        [P(9, 20.0)] * 3 + [P(8, 20.0)],
        streak=1,
    )
    assert target == Target(8, 20.0), why
    assert target.spread(4) == "8"
    assert "take 1 rep off" in why, "no set to name when they all moved"


def test_easing_still_stops_at_the_bottom_of_the_range():
    """Out of range is out of range, and then the load comes off."""
    target, why = next_target(
        WHOLE_SET_SQUAT, Target(6, 20.0), [P(5, 20.0)] * 4, streak=1
    )
    assert target == Target(6, 17.5), why


# --- how a target is written ----------------------------------------------


def test_a_flat_target_reads_as_one_figure():
    assert Target(8, 20.0).spread(4) == "8"
    assert Target(8, 20.0).per_set(4) == [8, 8, 8, 8]


def test_a_ramped_target_reads_hardest_first():
    assert Target(8, 20.0, lead=2).per_set(4) == [9, 9, 8, 8]


def test_a_lead_wider_than_the_sets_is_clamped():
    """A hand-edited workout should not produce a five-set squat."""
    assert Target(8, 20.0, lead=9).per_set(4) == [9, 9, 9, 9]


def test_a_lead_on_every_set_reads_as_the_flat_target_it_is():
    """`8+4` of four sets would name four sets of eight that are not there:
    every set is asked for nine, which is what `per_set` builds."""
    assert Target(8, 20.0, lead=4).per_set(4) == [9, 9, 9, 9]
    assert Target(8, 20.0, lead=4).spread(4) == "9"
    assert Target(8, 20.0, lead=9).spread(4) == "9"
    assert Target(16, 4.0, lead=4).spread(4, rep_step=2) == "18"


# --- what counts as a hit -------------------------------------------------


def test_hit_needs_every_set_to_clear_the_base():
    assert not hit(FOUR_SET_SQUAT, Target(8, 20.0), [8, 8, 8, 7])
    assert hit(FOUR_SET_SQUAT, Target(8, 20.0), [8, 8, 8, 8])


def test_hit_needs_enough_sets_at_the_higher_figure():
    ramped = Target(8, 20.0, lead=2)
    assert not hit(FOUR_SET_SQUAT, ramped, [9, 8, 8, 8])
    assert hit(FOUR_SET_SQUAT, ramped, [9, 9, 8, 8])


def test_hit_does_not_care_which_order_the_sets_were_logged_in():
    """The watch logs what you did, not which set was meant to be the hard one."""
    assert hit(FOUR_SET_SQUAT, Target(8, 20.0, lead=2), [8, 9, 9, 8])


def test_nothing_logged_is_not_a_hit():
    assert not hit(FOUR_SET_SQUAT, Target(8, 20.0), [])


# --- counting the streak --------------------------------------------------


def missed(reps, weight=20.0, target=8, sets=4):
    return Session(Target(target, weight), [P(reps, weight)] * sets)


def test_no_history_is_no_streak():
    assert miss_streak(FOUR_SET_SQUAT, [], 20.0) == 0


def test_the_streak_stops_at_the_first_session_that_hit():
    history = [missed(7), missed(8), missed(7)]
    assert miss_streak(FOUR_SET_SQUAT, history, 20.0) == 1


def test_consecutive_misses_accumulate():
    assert miss_streak(FOUR_SET_SQUAT, [missed(7), missed(6)], 20.0) == 2


def test_the_streak_stops_at_a_change_of_load():
    """A different weight is a different ladder; its misses say nothing here."""
    history = [missed(7), missed(7, weight=17.5), missed(7, weight=17.5)]
    assert miss_streak(FOUR_SET_SQUAT, history, 20.0) == 1


def test_the_streak_is_bounded_by_the_set_count():
    """Past sets - 1 the advance is pinned at one, so deeper history is moot."""
    assert miss_streak(FOUR_SET_SQUAT, [missed(7)] * 9, 20.0) == 3


def test_the_streak_stops_at_a_session_with_nothing_logged():
    history = [missed(7), Session(Target(8, 20.0), []), missed(7)]
    assert miss_streak(FOUR_SET_SQUAT, history, 20.0) == 1


def test_a_ramped_target_is_judged_as_a_ramp_when_counting_the_streak():
    """8,8,8,8 against 9,9,8,8 is a miss, and has to read as one here too."""
    history = [Session(Target(8, 20.0, lead=2), [P(8, 20.0)] * 4)]
    assert miss_streak(FOUR_SET_SQUAT, history, 20.0) == 1


# --- deloading ------------------------------------------------------------
#
# Missing once is a bad day and repeats. Missing the same target twice is a
# stall, and something has to give: the rep range first, because that is what a
# double progression has to spend, and the load only once the range is gone.

BARBELL_SQUAT = spec(sets=3, weight_step=2.5, min_weight=12.0)
LIGHT_RAISE = spec(
    name="Dumbbell Lateral Raise",
    garmin_name="DUMBBELL_LATERAL_RAISE",
    garmin_category="LATERAL_RAISE",
    rep_low=12,
    rep_high=20,
    sets=3,
    load="dumbbell",
    weight_step=1.0,
    min_weight=1.0,
)


def test_the_first_miss_is_a_bad_day_and_repeats():
    target, why = next_target(BARBELL_SQUAT, Target(9, 20.0), [P(8, 20.0)] * 3)
    assert target == Target(9, 20.0), why
    assert "missed target" in why


def test_the_second_miss_in_a_row_eases_the_target():
    """`9` missed twice becomes `8+2` - one set easier, where you landed."""
    target, why = next_target(
        BARBELL_SQUAT, Target(9, 20.0), [P(9, 20.0), P(9, 20.0), P(8, 20.0)], streak=1
    )
    assert target == Target(8, 20.0, lead=2), why
    assert target.spread(3) == "8+2"
    assert "ease" in why


def test_a_ramped_target_eases_by_one_rung_too():
    four = spec(sets=4, weight_step=2.5)
    target, _ = next_target(
        four,
        Target(8, 20.0, lead=2),
        [P(9, 20.0), P(8, 20.0), P(8, 20.0), P(8, 20.0)],
        streak=1,
    )
    assert target.per_set(4) == [9, 8, 8, 8]


def test_a_bad_miss_drops_straight_to_what_the_session_managed():
    """No point crawling down a rung a session when you are four short."""
    target, why = next_target(
        BARBELL_SQUAT, Target(10, 20.0), [P(7, 20.0)] * 3, streak=1
    )
    assert target == Target(7, 20.0), why


def test_easing_never_goes_below_the_bottom_of_the_range():
    target, _ = next_target(BARBELL_SQUAT, Target(7, 20.0), [P(3, 20.0)] * 3, streak=1)
    assert target == Target(6, 20.0), "rep_low, not the 3 that was managed"


def test_at_the_bottom_of_the_range_the_weight_comes_off():
    target, why = next_target(
        BARBELL_SQUAT, Target(6, 20.0), [P(5, 20.0)] * 3, streak=1
    )
    assert target == Target(6, 17.5), why
    assert "take a step off the load" in why


def test_a_deload_climbs_the_range_again_rather_than_starting_at_the_top():
    """Not rep_high: one good session there would earn the weight straight back."""
    target, _ = next_target(BARBELL_SQUAT, Target(6, 20.0), [P(5, 20.0)] * 3, streak=1)
    assert target.reps == BARBELL_SQUAT.rep_low
    assert target.lead == 0


def test_the_weight_never_goes_below_the_minimum():
    """A 1 kg step off a 1 kg dumbbell is a weight that does not exist."""
    target, why = next_target(LIGHT_RAISE, Target(12, 1.0), [P(10, 1.0)] * 3, streak=1)
    assert target == Target(12, 1.0), why
    assert "already at the 1 kg minimum" in why


def test_a_bodyweight_stall_has_nothing_to_take_off():
    target, why = next_target(PLANK, Target(30, 0.0), held(28, 28, 28), streak=1)
    assert target == Target(30, 0.0), why
    assert "no load to take off" in why


def test_a_stall_at_a_different_load_is_not_a_deload():
    """A miss at a weight you were not prescribed is rule 5's business."""
    target, why = next_target(LIGHT_RAISE, Target(13, 3.0), [P(8, 4.0)] * 3, streak=1)
    assert target == Target(13, 3.0), why
    assert "below the 12-20 range" in why


def test_a_short_session_consolidates_rather_than_deloading():
    _, why = next_target(
        BARBELL_SQUAT, Target(9, 20.0), [P(8, 20.0), P(8, 20.0)], streak=1
    )
    assert "consolidate" in why


def test_easing_steps_by_rep_step():
    """Per-side counting eases 20,20,20,20 to 20,20,20,18, both sides even."""
    target, _ = next_target(
        LUNGE_DOUBLED, Target(20, 4.0), [P(20, 4.0)] * 3 + [P(18, 4.0)], streak=1
    )
    assert target.per_set(4, rep_step=2) == [20, 20, 20, 18]


def test_a_deload_and_the_climb_back_are_the_same_ladder():
    """Ease down, then hit it: the granular advance takes it straight back."""
    eased, _ = next_target(
        BARBELL_SQUAT, Target(9, 20.0), [P(9, 20.0), P(9, 20.0), P(8, 20.0)], streak=1
    )
    assert eased.spread(3) == "8+2"

    back, _ = next_target(
        BARBELL_SQUAT, eased, [P(9, 20.0), P(9, 20.0), P(8, 20.0)], streak=0
    )
    assert back == Target(9, 20.0), "levelled up, flat again"


# --- topping out ----------------------------------------------------------
#
# The mirror of the deload's floor. Rule 3 adds a step every time the range is
# cleared, and equipment you own runs out; without a ceiling the target climbs
# to a weight no session can be logged against.

HOME_PRESS = spec(
    name="Dumbbell Floor Press",
    garmin_name="DUMBBELL_FLOOR_PRESS",
    garmin_category="BENCH_PRESS",
    rep_low=10,
    rep_high=16,
    sets=3,
    load="dumbbell",
    weight_step=2.5,
    min_weight=2.5,
    max_weight=10.0,
)


def test_the_weight_never_goes_above_the_maximum():
    """12.5 kg dumbbells you do not own is a target no session can answer."""
    target, why = next_target(HOME_PRESS, Target(16, 10.0), [P(16, 10.0)] * 3)
    assert target == Target(16, 10.0), why
    assert "already at the 10 kg maximum" in why


def test_a_capped_exercise_holds_at_the_top_of_the_range():
    """The same ending as bodyweight: the range is all there is left."""
    target, _ = next_target(HOME_PRESS, Target(16, 10.0), [P(18, 10.0)] * 3)
    assert target == Target(HOME_PRESS.rep_high, 10.0)


def test_the_last_step_is_shortened_to_land_on_the_maximum():
    """9 + 2.5 is 11.5, which does not exist - but the 10 kg pair does."""
    target, why = next_target(HOME_PRESS, Target(16, 9.0), [P(16, 9.0)] * 3)
    assert target == Target(HOME_PRESS.rep_low, 10.0), why
    assert "up to the 10 kg maximum" in why


def test_the_shortened_step_is_taken_once_and_then_holds():
    """Reaching the ceiling is a rung to climb, not a place to hover below."""
    stepped, why = next_target(HOME_PRESS, Target(16, 9.0), [P(16, 9.0)] * 3)
    assert stepped == Target(10, 10.0), why

    held, why = next_target(HOME_PRESS, stepped, [P(16, 10.0)] * 3)
    assert held == Target(HOME_PRESS.rep_high, 10.0), why


def test_a_load_already_past_the_maximum_is_not_pulled_down():
    """Rule 3 is the rule that adds load; taking it off is the deload's job."""
    target, why = next_target(HOME_PRESS, Target(16, 12.5), [P(16, 12.5)] * 3)
    assert target == Target(16, 12.5), why
    assert "past the 10 kg maximum" in why, "past it, rather than at it"


def test_below_the_maximum_the_weight_still_climbs():
    """The ceiling is a stop, not a brake: every step under it is taken."""
    target, why = next_target(HOME_PRESS, Target(16, 7.5), [P(16, 7.5)] * 3)
    assert target == Target(HOME_PRESS.rep_low, 10.0), why


def test_no_maximum_means_the_weight_keeps_climbing():
    """The default, and what every config written before ceilings did."""
    assert SQUAT.max_weight is None
    target, _ = next_target(SQUAT, Target(10, 20.0), [P(10, 20.0)] * 3)
    assert target == Target(SQUAT.rep_low, 25.0)


def test_a_capped_exercise_still_deloads():
    """Running out of weight upwards says nothing about coming back down."""
    target, why = next_target(HOME_PRESS, Target(10, 10.0), [P(8, 10.0)] * 3, streak=1)
    assert target == Target(HOME_PRESS.rep_low, 7.5), why


def test_a_session_at_the_cap_rebases_off_an_impossible_target():
    """The way back from a target already past the ceiling: lift what you have."""
    target, why = next_target(HOME_PRESS, Target(10, 12.5), [P(12, 10.0)] * 3)
    assert target.weight == 10.0, why


# --- a session split across two loads --------------------------------------
#
# The set count and the rep count ask different questions. Counting only the
# sets at the working weight answers the second and gets the first wrong: a
# whole session done at two loads reads as an abandoned one.

CALF_RAISE = spec(
    name="Seated Calf Raise",
    garmin_name="SEATED_CALF_RAISE",
    garmin_category="CALF_RAISE",
    rep_low=12,
    rep_high=18,
    sets=3,
    load="dumbbell",
    weight_step=2.5,
    min_weight=2.5,
)


def test_a_heavier_set_counts_toward_the_prescribed_sets():
    """Two at 20 kg and one at 30 is three sets, not two."""
    target, why = next_target(
        CALF_RAISE, Target(17, 20.0), [P(17, 20.0), P(17, 20.0), P(17, 30.0)]
    )
    assert target == Target(18, 20.0), why
    assert "consolidate" not in why


def test_a_heavier_set_moves_the_reps_and_not_the_load():
    """One set at 30 kg is a third of the session; rule 5's business."""
    target, _ = next_target(
        CALF_RAISE, Target(17, 20.0), [P(17, 20.0), P(17, 20.0), P(17, 30.0)]
    )
    assert target.weight == 20.0


def test_a_heavier_set_that_came_up_short_counts_for_nothing():
    """A failed top set must not read as a completed session."""
    _, why = next_target(
        CALF_RAISE, Target(17, 20.0), [P(17, 20.0), P(17, 20.0), P(5, 30.0)]
    )
    assert "only 2 of 3 sets logged, consolidate" in why


def test_a_genuinely_short_session_still_consolidates():
    """The guard the heavier set slips past is still there for real misses."""
    _, why = next_target(CALF_RAISE, Target(17, 20.0), [P(17, 20.0), P(17, 20.0)])
    assert "only 2 of 3 sets logged, consolidate" in why


def test_a_lighter_set_does_not_count():
    """Easier than asked is not the same as harder than asked."""
    _, why = next_target(
        CALF_RAISE, Target(17, 20.0), [P(17, 20.0), P(17, 20.0), P(17, 10.0)]
    )
    assert "only 2 of 3 sets logged, consolidate" in why


def test_a_heavier_set_can_complete_an_uneven_target():
    """18,17,18 against `17+2` is what was asked, whichever load carried it."""
    target, why = next_target(
        CALF_RAISE, Target(17, 20.0, lead=2), [P(18, 20.0), P(17, 20.0), P(18, 30.0)]
    )
    assert "missed" not in why, why
    assert target == Target(18, 20.0), why


def test_the_heavier_set_never_flatters_the_reps():
    """It contributes its own count, so it cannot lift the floor off a weak set."""
    target, why = next_target(
        CALF_RAISE, Target(15, 20.0), [P(15, 20.0), P(15, 20.0), P(18, 30.0)]
    )
    assert target == Target(16, 20.0), why


# --- load groups ----------------------------------------------------------

FLYE = spec(
    name="Incline Dumbbell Flye",
    rep_low=10,
    rep_high=14,
    weight_step=1.0,
    min_weight=1.0,
    max_weight=40.0,
    tiers=(LoadTier(1.0, 10.0, (1.0,)), LoadTier(12.0, 40.0, (2.0,))),
)


def test_topping_out_a_rack_graduates_to_the_next_one():
    """10 kg is the heaviest pair on the small rack, so rule 3 crosses over."""
    target, why = next_target(FLYE, Target(14, 10.0), [P(14, 10.0)] * 3)
    assert target == Target(10, 12.0), why
    assert "onto the next rack at 12 kg" in why


def test_climbing_within_a_rack_says_nothing_about_racks():
    target, why = next_target(FLYE, Target(14, 12.0), [P(14, 12.0)] * 3)
    assert target == Target(10, 14.0), why
    assert "rack" not in why


def test_a_stall_after_graduating_drops_back_to_the_rack_below():
    """Crossing is reversible: 12 kg proved too heavy, so 10 kg is offered."""
    target, why = next_target(FLYE, Target(10, 12.0), [P(8, 12.0)] * 3, streak=1)
    assert target == Target(10, 10.0), why
    assert "take a step off the load" in why


def test_the_top_of_the_heaviest_rack_is_the_end_of_the_load():
    target, why = next_target(FLYE, Target(14, 40.0), [P(14, 40.0)] * 3)
    assert target == Target(14, 40.0), why
    assert "already at the 40 kg maximum" in why


def test_the_bottom_of_the_lightest_rack_refuses_a_deload():
    target, why = next_target(FLYE, Target(10, 1.0), [P(8, 1.0)] * 3, streak=1)
    assert target == Target(10, 1.0), why
    assert "already at the 1 kg minimum" in why


def test_the_increment_chosen_grows_with_the_load():
    """One stack, three plate sizes: the jump matches what is already on it."""
    stack = spec(
        rep_low=10,
        rep_high=15,
        weight_step=1.25,
        min_weight=5.0,
        tiers=(LoadTier(5.0, None, (1.25, 2.5, 5.0)),),
    )
    light, _ = next_target(stack, Target(15, 5.0), [P(15, 5.0)] * 3)
    heavy, _ = next_target(stack, Target(15, 45.0), [P(15, 45.0)] * 3)
    assert light == Target(10, 6.25)
    assert heavy == Target(10, 50.0)
