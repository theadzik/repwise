"""Reporting drift between the config and Garmin."""

from builders import catalog, payload, rep_step, repeat, rest_step, spec

from repwise.checker import check_catalog, check_programming, check_workout
from repwise.domain.models import Workout

SQUAT_GROUP = repeat(
    rep_step("BARBELL_BACK_SQUAT", "SQUAT", 6, 30.0), sets=4, rest=120.0
)


def configured(*exercises, key="Workout A"):
    return Workout(key, "1", ["workout a"], list(exercises))


SQUAT_SPEC = spec(sets=4, rest=120)


def test_a_matching_workout_reports_nothing():
    findings = check_workout(configured(SQUAT_SPEC), payload(SQUAT_GROUP))
    assert findings == []


def test_two_exercises_sharing_a_category_are_reported_as_a_rebuild():
    """Sharing a category does not make them the same movement.

    The step is rebuilt rather than quietly reused, so the warning has to say
    that the progression restarts - the cost of the swap, not a footnote.
    """
    wrong = spec(garmin_name="WEIGHTED_BARBELL_BACK_SQUAT", sets=4, rest=120)
    findings = check_workout(configured(wrong), payload(SQUAT_GROUP))

    assert len(findings) == 1
    detail = findings[0].detail
    assert "config says WEIGHTED_BARBELL_BACK_SQUAT" in detail
    assert "Garmin says BARBELL_BACK_SQUAT" in detail
    assert "different exercises" in detail
    assert "restart its progression" in detail
    assert findings[0].severity == "error"


def test_an_exercise_missing_from_garmin_is_an_error():
    absent = spec(
        name="Face Pull", garmin_name="FACE_PULL", garmin_category="ROW", sets=4
    )
    findings = check_workout(configured(absent), payload(SQUAT_GROUP))
    assert any(f.severity == "error" for f in findings)
    assert "not in the Garmin workout" in findings[0].detail


def test_an_ambiguous_category_is_an_error():
    """Two exercises share the category, so it cannot disambiguate."""
    front = repeat(rep_step("FRONT_SQUAT", "SQUAT", 8, 20.0), sets=3)
    wrong = spec(garmin_name="NOT_IN_GARMIN", garmin_category="SQUAT", sets=4)
    findings = check_workout(configured(wrong), payload(SQUAT_GROUP, front))
    assert any("ambiguous" in f.detail for f in findings)


# --- against Garmin's catalog, rather than against one workout -------------
#
# The prior question: not "does this workout hold it" but "does it exist". The
# only check that says anything useful about a workout Garmin has never seen.

KNOWN = catalog(
    SQUAT=("BARBELL_BACK_SQUAT", "FRONT_SQUAT"),
    DEADLIFT=("BARBELL_DEADLIFT", "DUMBBELL_DEADLIFT"),
    PLANK=("PLANK",),
)


def only(*exercises) -> str:
    """The one finding those exercises produce, as its detail."""
    findings = check_catalog(configured(*exercises), KNOWN)
    assert len(findings) == 1, findings
    assert findings[0].severity == "error"
    return findings[0].detail


def test_a_pair_garmin_has_is_not_a_finding():
    assert check_catalog(configured(SQUAT_SPEC), KNOWN) == []


def test_an_invented_name_is_reported():
    absent = spec(name="Nonsense", garmin_name="WAT", garmin_category="SQUAT")
    assert "WAT is not an exercise Garmin has" in only(absent)


def test_a_typo_is_offered_the_name_it_nearly_is():
    typo = spec(garmin_name="BARBELL_DEADLIFTT", garmin_category="DEADLIFT")
    assert "Did you mean BARBELL_DEADLIFT" in only(typo)


def test_an_invented_category_is_named_alongside_the_name():
    """Both halves wrong is a pair invented, not a spelling to go hunting for."""
    bogus = spec(garmin_name="WAT", garmin_category="BOGUS")
    assert "neither WAT nor the category BOGUS" in only(bogus)


def test_a_real_exercise_under_the_wrong_category_says_which_to_use():
    misfiled = spec(garmin_name="BARBELL_DEADLIFT", garmin_category="SQUAT")
    detail = only(misfiled)

    assert "filed under DEADLIFT, not SQUAT" in detail
    assert "set garmin_category: DEADLIFT" in detail


def test_the_wrong_case_is_a_spelling_not_a_misfiling():
    """The category is right; saying it is wrong would send you to fix nothing."""
    shouting = spec(garmin_name="barbell_back_squat", garmin_category="SQUAT")
    detail = only(shouting)

    assert "spells barbell_back_squat as BARBELL_BACK_SQUAT" in detail
    assert "filed under" not in detail


def test_both_halves_wrong_is_answered_with_the_whole_pair():
    muddled = spec(garmin_name="barbell_deadlift", garmin_category="SQUAT")
    assert "what it has is DEADLIFT/BARBELL_DEADLIFT" in only(muddled)


def test_a_missing_category_is_not_itself_a_finding():
    """Declaring one is optional - matching only falls back to it."""
    bare = spec(garmin_name="PLANK", garmin_category=None)
    assert check_catalog(configured(bare), KNOWN) == []


def test_a_missing_category_still_checks_the_name():
    bare = spec(garmin_name="WAT", garmin_category=None)
    assert "WAT is not an exercise Garmin has" in only(bare)


def test_a_missing_category_still_checks_the_spelling():
    bare = spec(garmin_name="Plank", garmin_category=None)
    assert "spells Plank as PLANK" in only(bare)


def test_every_bad_exercise_is_reported_not_just_the_first():
    """One run should be enough to fix the file."""
    findings = check_catalog(
        configured(
            spec(name="One", garmin_name="WAT", garmin_category="SQUAT"),
            SQUAT_SPEC,
            spec(name="Two", garmin_name="ALSO_WAT", garmin_category="SQUAT"),
        ),
        KNOWN,
    )
    assert [f.detail.split(":")[0] for f in findings] == ["One", "Two"]


# --- what `update` owns, and this no longer repeats ------------------------
#
# All of it used to be reported here, back when `update` only moved targets and
# these really were drift. Now the config decides them and `update` applies
# them, so repeating them would be telling the user off for not having run it.


def test_a_set_count_the_config_will_set_is_not_a_finding():
    findings = check_workout(configured(spec(sets=3, rest=120)), payload(SQUAT_GROUP))
    assert findings == []


def test_a_rest_the_config_will_set_is_not_a_finding():
    findings = check_workout(configured(spec(sets=4, rest=90)), payload(SQUAT_GROUP))
    assert findings == []


def test_a_lap_button_rest_between_sets_is_not_a_finding():
    """`update` reports this one itself, at the moment it declines to change it."""
    button = repeat(rep_step("BARBELL_BACK_SQUAT", "SQUAT", 6, 30.0), sets=4)
    button["workoutSteps"][1] = rest_step()

    assert check_workout(configured(SQUAT_SPEC), payload(button)) == []


def test_an_exercise_the_config_dropped_is_not_a_finding():
    """It is a removal `update` will make and report, not a mistake."""
    curls = repeat(rep_step("DUMBBELL_BICEPS_CURL", "CURL", 10, 8.0), sets=3)

    assert check_workout(configured(SQUAT_SPEC), payload(SQUAT_GROUP, curls)) == []


# --- ranges too wide for what their step is really worth --------------------


CALF_GROUP = repeat(
    rep_step("WEIGHTED_STANDING_CALF_RAISE", "CALF_RAISE", 20, 20.0), sets=3
)

CALF_SPEC = spec(
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


def test_a_range_too_wide_for_its_real_step_is_reported():
    findings = check_programming(
        configured(CALF_SPEC), payload(CALF_GROUP), bodyweight=80.0
    )

    assert len(findings) == 1
    detail = findings[0].detail
    assert "100 kg" in detail  # the stack plus the lifter, not the stack
    assert "drop in effort" in detail
    assert "make it 12-14" in detail, "the top that breaks even, not the stack"


def test_the_suggestion_shows_how_much_room_there_is_around_it():
    """One number reads as the only answer. The tolerance is a band, and a
    range already inside it is not worth rewriting to the decimal."""
    findings = check_programming(
        configured(CALF_SPEC), payload(CALF_GROUP), bodyweight=80.0
    )

    assert "anything from 12-13 to 12-18 fits" in findings[0].detail


def test_the_bottom_of_the_range_is_never_suggested_away():
    """Whatever is wrong with the arithmetic, `rep_low` is a decision about
    how heavy the exercise gets and stays where it was put."""
    findings = check_programming(
        configured(CALF_SPEC), payload(CALF_GROUP), bodyweight=80.0
    )

    assert findings[0].detail.count("12-") == 3, "every range offered starts at 12"


def test_the_same_range_is_fine_once_bodyweight_is_not_claimed():
    """A lat pull-down is categorised PULL_UP and carries none of you."""
    seated = spec(**{**CALF_SPEC.__dict__, "bodyweight_factor": 0.0})

    assert check_programming(configured(seated), payload(CALF_GROUP), 80.0) == []


def test_an_ordinary_barbell_range_is_not_reported():
    """The sawtooth is inherent to double progression; only excess is news."""
    assert check_programming(configured(SQUAT_SPEC), payload(SQUAT_GROUP), 80.0) == []


def test_without_a_bodyweight_the_exercise_says_it_was_skipped():
    findings = check_programming(
        configured(CALF_SPEC), payload(CALF_GROUP), bodyweight=None
    )

    assert len(findings) == 1
    assert "not checked" in findings[0].detail


def test_an_exercise_garmin_does_not_hold_is_left_to_the_other_check():
    """Reporting it twice would bury the finding that matters."""
    absent = spec(**{**CALF_SPEC.__dict__, "garmin_name": "NOT_IN_GARMIN"})
    absent = spec(**{**absent.__dict__, "garmin_category": "NOT_A_CATEGORY"})

    assert check_programming(configured(absent), payload(SQUAT_GROUP), 80.0) == []


LUNGE_GROUP = repeat(rep_step("ALTERNATING_DUMBBELL_LUNGE", "LUNGE", 24, 24.0), sets=4)

LUNGE_SPEC = spec(
    name="Alternating Dumbbell Lunge",
    garmin_name="ALTERNATING_DUMBBELL_LUNGE",
    garmin_category="LUNGE",
    rep_low=16,
    rep_high=24,
    rep_step=2,
    sets=4,
    load="dumbbell",
    weight_step=2.0,
    bodyweight_factor=0.85,
)


def test_a_range_counted_per_side_is_judged_per_side():
    """8-12 per leg, which a 2 kg step pays for. Read as 16-24 it looks broken."""
    findings = check_programming(
        configured(LUNGE_SPEC), payload(LUNGE_GROUP), bodyweight=80.0
    )

    assert findings == []


def test_the_pair_is_read_as_entered():
    """No implement counting: what the watch holds is the load, whole."""
    wide = spec(**{**LUNGE_SPEC.__dict__, "rep_high": 40})

    findings = check_programming(
        configured(wide), payload(LUNGE_GROUP), bodyweight=80.0
    )

    assert len(findings) == 1
    # 24 kg of dumbbell as entered, plus 0.85 of 80 kg.
    assert "+2 kg on 92 kg" in findings[0].detail
