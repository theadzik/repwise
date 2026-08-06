"""Reporting drift between the config and Garmin."""

from builders import payload, rep_step, repeat, rest_step, spec

from workout.checker import check_workout
from workout.domain.models import Workout

SQUAT_GROUP = repeat(
    rep_step("BARBELL_BACK_SQUAT", "SQUAT", 6, 30.0), sets=4, rest=120.0
)


def configured(*exercises, key="Workout A"):
    return Workout(key, "1", ["workout a"], list(exercises))


SQUAT_SPEC = spec(sets=4, rest=120)


def test_a_matching_workout_reports_nothing():
    findings = check_workout(configured(SQUAT_SPEC), payload(SQUAT_GROUP))
    assert findings == []


def test_a_wrong_garmin_name_rescued_by_category_is_reported():
    """The real drift this was written for: it works, but only by luck."""
    wrong = spec(garmin_name="WEIGHTED_BARBELL_BACK_SQUAT", sets=4, rest=120)
    findings = check_workout(configured(wrong), payload(SQUAT_GROUP))

    assert len(findings) == 1
    detail = findings[0].detail
    assert "config says WEIGHTED_BARBELL_BACK_SQUAT" in detail
    assert "Garmin says BARBELL_BACK_SQUAT" in detail
    assert "Matched by category" in detail


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
