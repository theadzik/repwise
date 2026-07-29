"""Shared fixtures.

Only pytest plumbing belongs here. The builders and the sample payloads are
plain functions and constants in builders.py, imported by name.
"""

import pytest


@pytest.fixture
def write_config(tmp_path):
    """Write a workouts.yaml into a temp dir and return its path."""

    def _write(text: str) -> str:
        path = tmp_path / "workouts.yaml"
        path.write_text(text)
        return str(path)

    return _write
