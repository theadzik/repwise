"""Shared fixtures.

Only pytest plumbing belongs here. The builders and the sample payloads are
plain functions and constants in builders.py, imported by name.
"""

import pytest

from repwise.garmin import catalog


@pytest.fixture(autouse=True)
def no_cached_catalog(monkeypatch):
    """Keep the developer's own exercise catalog out of the suite.

    `check` and `update` both read it, and the default token store points at
    the real one under ~/.config, so without this the tests would answer
    differently on a machine that happened to have it cached. Absent rather
    than stubbed: the tests that want a catalog install one, and the rest
    should be exercising the path where there is none.
    """
    monkeypatch.setattr(catalog, "load", lambda _settings: None)


@pytest.fixture
def write_config(tmp_path):
    """Write a workouts.yaml into a temp dir and return its path."""

    def _write(text: str) -> str:
        path = tmp_path / "workouts.yaml"
        path.write_text(text)
        return str(path)

    return _write
