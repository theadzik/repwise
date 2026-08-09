"""Garmin's exercise catalog: what it says, and how it is cached.

Nothing here reaches the network. `download` is the one function that would,
and every test that needs a catalog either builds one or replaces that
function, which is what keeps the suite runnable on a train.
"""

import json
from pathlib import Path

import pytest
from builders import catalog, catalog_payload

from repwise.domain.models import GarminSettings
from repwise.errors import GarminError
from repwise.garmin import catalog as module


@pytest.fixture(autouse=True)
def no_cached_catalog():
    """Opt out of the suite-wide stub: the cache is what this module tests.

    Safe here and nowhere else, because every store below is a tmp_path and
    every download is replaced.
    """


@pytest.fixture
def settings(tmp_path):
    """A token store that does not exist yet, as on a first run."""
    return GarminSettings(token_store=str(tmp_path / "store"))


@pytest.fixture
def served(monkeypatch):
    """Answer `download` with a payload, and count how often it was asked."""

    def install(payload=None, failure=None):
        calls = []

        def fake_download():
            calls.append(True)
            if failure:
                raise failure
            return payload if payload is not None else catalog_payload(SQUAT=("A",))

        monkeypatch.setattr(module, "download", fake_download)
        return calls

    return install


# --- what the catalog says -------------------------------------------------


def test_it_reads_categories_and_their_exercises():
    parsed = catalog(SQUAT=("BARBELL_BACK_SQUAT",), PLANK=("PLANK",))

    assert len(parsed) == 2
    assert parsed.has_category("SQUAT")
    assert parsed.holds("SQUAT", "BARBELL_BACK_SQUAT")


def test_an_exercise_belongs_to_its_own_category_only():
    """The pair is the unit: Garmin validates the two against each other."""
    parsed = catalog(SQUAT=("BARBELL_BACK_SQUAT",), PLANK=("PLANK",))

    assert not parsed.holds("PLANK", "BARBELL_BACK_SQUAT")
    assert not parsed.holds("NOT_A_CATEGORY", "BARBELL_BACK_SQUAT")


def test_locate_gives_back_garmins_own_spelling():
    """Asked in lower case, so that the answer is the correction."""
    parsed = catalog(SQUAT=("BARBELL_BACK_SQUAT",))

    assert parsed.locate("barbell_back_squat") == [("SQUAT", "BARBELL_BACK_SQUAT")]


def test_locate_finds_a_name_filed_under_several_categories():
    parsed = catalog(SQUAT=("LUNGE",), LUNGE=("LUNGE",))

    assert parsed.locate("LUNGE") == [("LUNGE", "LUNGE"), ("SQUAT", "LUNGE")]


def test_locate_is_empty_for_something_invented():
    assert catalog(SQUAT=("BARBELL_BACK_SQUAT",)).locate("NOPE") == []


def test_near_misses_are_offered_for_a_typo():
    parsed = catalog(DEADLIFT=("BARBELL_DEADLIFT", "DUMBBELL_DEADLIFT"))

    assert "BARBELL_DEADLIFT" in parsed.like("BARBELL_DEADLIFTT")


def test_nothing_is_offered_for_a_name_with_no_neighbours():
    """A loose match would name three exercises with nothing to do with it."""
    parsed = catalog(DEADLIFT=("BARBELL_DEADLIFT",))

    assert parsed.like("WAT") == []


def test_a_payload_with_no_categories_is_refused():
    """An empty catalog would report every exercise in the config as unknown."""
    with pytest.raises(GarminError):
        module.ExerciseCatalog.parse({"categories": {}})

    with pytest.raises(GarminError):
        module.ExerciseCatalog.parse({"nothing": "expected"})


# --- the cache -------------------------------------------------------------


def test_nothing_is_cached_to_begin_with(settings):
    assert module.load(settings) is None


def test_saving_creates_the_token_store(settings):
    """The catalog can be fetched before the first login has made that store."""
    path = module.save(settings, catalog_payload(SQUAT=("A",)))

    assert json.loads(Path(path).read_text())["categories"]["SQUAT"]["exercises"] == {
        "A": {"primaryMuscles": ["ABS"], "secondaryMuscles": []}
    }


def test_what_was_saved_is_what_loads_again(settings):
    module.save(settings, catalog_payload(SQUAT=("A",), PLANK=("PLANK",)))

    loaded = module.load(settings)
    assert loaded is not None
    assert loaded.holds("PLANK", "PLANK")


def test_the_whole_payload_is_kept_not_just_what_is_read(settings):
    """The muscle groups are the reason to store the file rather than a digest."""
    module.save(settings, catalog_payload(SQUAT=("A",)))

    kept = json.loads(Path(module.cache_path(settings)).read_text())
    assert kept["categories"]["SQUAT"]["exercises"]["A"]["primaryMuscles"] == ["ABS"]


def test_a_truncated_cache_reads_as_absent(settings):
    """It is a disposable copy of a public file, so the repair is to fetch it."""
    module.save(settings, catalog_payload(SQUAT=("A",)))
    Path(module.cache_path(settings)).write_text("{ truncated")

    assert module.load(settings) is None


# --- what a command gets ---------------------------------------------------


def test_a_cached_catalog_is_handed_over(settings):
    module.save(settings, catalog_payload(SQUAT=("BARBELL_BACK_SQUAT",)))

    found = module.optional(settings, "names went unchecked")
    assert found is not None
    assert found.holds("SQUAT", "BARBELL_BACK_SQUAT")


def test_nothing_cached_is_not_an_error(settings):
    """Every caller is worth running degraded rather than blocked."""
    assert module.optional(settings, "names went unchecked") is None


def test_the_warning_says_the_cost_and_the_cure(settings, caplog):
    module.optional(settings, "names went unchecked")

    assert "names went unchecked" in caplog.text
    assert "repwise fetch exercises" in caplog.text


def test_reading_the_cache_never_downloads(settings, served):
    """`repwise fetch exercises` is the only command that reaches Garmin."""
    calls = served()

    assert module.optional(settings, "names went unchecked") is None
    assert calls == []


def test_a_corrupt_cache_reads_as_absent_rather_than_raising(settings):
    module.save(settings, catalog_payload(SQUAT=("A",)))
    Path(module.cache_path(settings)).write_text("{ truncated")

    assert module.optional(settings, "names went unchecked") is None


def test_the_cache_path_expands_a_home_relative_store():
    """The default store is a literal `~/.config/repwise`, and is joined raw.

    Left unexpanded it makes a directory called `~` wherever the process is
    standing, which is how the suite once wrote one into the repository.
    """
    path = module.cache_path(GarminSettings())

    assert not path.startswith("~")
    assert path == str(Path.home() / ".config" / "repwise" / module.CACHE_NAME)
