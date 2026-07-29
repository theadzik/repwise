"""Finding the exercise a name or a category refers to."""

from workout.domain.matching import ExerciseIndex, normalise


def test_normalise_bridges_the_two_naming_styles():
    assert normalise("BARBELL_BACK_SQUAT") == normalise("Barbell Back Squat")


def test_normalise_drops_punctuation_and_case():
    assert normalise("Farmer's Walk!") == "farmerswalk"


# --- names ----------------------------------------------------------------


def test_a_name_finds_its_item():
    index: ExerciseIndex[str] = ExerciseIndex()
    index.add("squat", name="BARBELL_BACK_SQUAT")
    assert index.by_name("Barbell Back Squat") == "squat"


def test_the_first_indexed_name_wins():
    """Callers pass what the payload said, then what the config declared."""
    index: ExerciseIndex[str] = ExerciseIndex()
    index.add("squat", name="BARBELL_BACK_SQUAT")
    assert index.by_name("MISSING", "BARBELL_BACK_SQUAT") == "squat"


def test_an_alias_does_not_displace_an_authoritative_name():
    """Two exercises, where one's friendly name is another's Garmin name."""
    index: ExerciseIndex[str] = ExerciseIndex()
    index.add("first", aliases=("Overhead Press",))
    index.add("second", name="OVERHEAD_PRESS")
    assert index.by_name("overhead press") == "second"


def test_an_empty_name_matches_nothing():
    index: ExerciseIndex[str] = ExerciseIndex()
    index.add("squat", name="BARBELL_BACK_SQUAT")
    assert index.by_name(None) is None
    assert index.by_name("") is None


# --- categories -----------------------------------------------------------


def test_a_category_identifies_the_only_item_claiming_it():
    index: ExerciseIndex[str] = ExerciseIndex()
    index.add("squat", name="BARBELL_BACK_SQUAT", category="SQUAT")
    assert index.by_category("SQUAT") == "squat"


def test_a_category_two_items_claim_identifies_neither():
    """It cannot say which of them a step belongs to, so it says nothing."""
    index: ExerciseIndex[str] = ExerciseIndex()
    index.add("back", name="BARBELL_BACK_SQUAT", category="SQUAT")
    index.add("front", name="BARBELL_FRONT_SQUAT", category="SQUAT")
    assert index.by_category("SQUAT") is None


def test_claiming_reports_the_ambiguity_the_lookup_hides():
    """`check` tells "not there" from "ambiguous", so it needs the candidates."""
    index: ExerciseIndex[str] = ExerciseIndex()
    index.add("back", name="BARBELL_BACK_SQUAT", category="SQUAT")
    index.add("front", name="BARBELL_FRONT_SQUAT", category="SQUAT")
    assert index.claiming("SQUAT") == ["back", "front"]
    assert index.claiming("BENCH_PRESS") == []
    assert index.claiming(None) == []


# --- the full lookup ------------------------------------------------------


def test_find_prefers_the_name_over_the_category():
    index: ExerciseIndex[str] = ExerciseIndex()
    index.add("squat", name="BARBELL_BACK_SQUAT", category="SQUAT")
    index.add("front", name="BARBELL_FRONT_SQUAT", category="LUNGE")
    assert index.find("BARBELL_FRONT_SQUAT", "SQUAT") == "front"


def test_find_falls_back_to_the_category_when_the_name_is_unknown():
    """Garmin logs the name it auto-detected, which need not be the one programmed."""
    index: ExerciseIndex[str] = ExerciseIndex()
    index.add("squat", name="BARBELL_BACK_SQUAT", category="SQUAT")
    assert index.find("SOMETHING_ELSE", "SQUAT") == "squat"


def test_find_gives_up_when_neither_matches():
    index: ExerciseIndex[str] = ExerciseIndex()
    index.add("squat", name="BARBELL_BACK_SQUAT", category="SQUAT")
    assert index.find("DEADLIFT", "HIP_HINGE") is None
