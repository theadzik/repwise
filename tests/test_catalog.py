"""Garmin's exercise catalog: what it says, and how it is cached.

Nothing here reaches the network. `download` is the one function that would,
and every test that needs a catalog either builds one or replaces that
function, which is what keeps the suite runnable on a train.
"""

import json
from pathlib import Path

import pytest
from builders import catalog, catalog_payload

from workout.domain.models import GarminSettings
from workout.errors import GarminError
from workout.garmin import catalog as module


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


# --- ensure ----------------------------------------------------------------


def test_the_first_run_downloads_and_caches(settings, served):
    calls = served(catalog_payload(SQUAT=("BARBELL_BACK_SQUAT",)))

    assert module.ensure(settings).holds("SQUAT", "BARBELL_BACK_SQUAT")
    assert len(calls) == 1
    assert module.load(settings) is not None


def test_a_later_run_costs_no_request(settings, served):
    calls = served()
    module.ensure(settings)

    module.ensure(settings)

    assert len(calls) == 1


def test_a_corrupt_cache_is_replaced_rather_than_reported(settings, served):
    served()
    module.ensure(settings)
    Path(module.cache_path(settings)).write_text("{ truncated")

    assert module.ensure(settings) is not None


def test_a_failed_download_is_not_cached(settings, served):
    served(failure=GarminError("no network"))

    with pytest.raises(GarminError):
        module.ensure(settings)
    assert module.load(settings) is None


def test_a_response_that_is_not_a_catalog_is_never_written(settings, served):
    """Parsed before it is saved, so a good cache survives a bad answer."""
    served({"unexpected": "shape"})

    with pytest.raises(GarminError):
        module.ensure(settings)
    assert module.load(settings) is None
