"""Shared fixtures.

Only pytest plumbing belongs here. The builders and the sample payloads are
plain functions and constants in builders.py, imported by name.
"""

import pytest

from repwise.errors import GarminError
from repwise.garmin import catalog


@pytest.fixture(autouse=True)
def no_catalog(monkeypatch):
    """Keep the exercise catalog out of every test that did not ask for one.

    `check` and `update` both fetch it, so without this a run would download
    Garmin's copy - or read whatever the developer had cached beside their own
    tokens, since the default store points there. Either way the suite would
    need a network and answer differently per machine. Refused rather than
    stubbed with a fixture catalog: the tests that want one install it
    themselves, and the rest should exercise the path where there is none.
    """

    def refuse(_settings):
        raise GarminError(
            "Could not download the exercise catalog: no network in tests"
        )

    monkeypatch.setattr(catalog, "ensure", refuse)


@pytest.fixture
def write_config(tmp_path):
    """Write a workouts.yaml into a temp dir and return its path."""

    def _write(text: str) -> str:
        path = tmp_path / "workouts.yaml"
        path.write_text(text)
        return str(path)

    return _write
