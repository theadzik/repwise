"""What `logout` deletes, what it keeps, and what it tells you."""

import logging

from repwise.app.logout import run_logout
from repwise.domain.models import GarminSettings
from repwise.errors import ExitCode


def cached(tmp_path) -> GarminSettings:
    """A token store in the state a previous login would have left it."""
    store = tmp_path / "tokens"
    store.mkdir()
    (store / "garmin_tokens.json").write_text("{}")
    return GarminSettings(token_store=str(store))


def report(settings, caplog) -> str:
    with caplog.at_level(logging.INFO, logger="repwise.app.logout"):
        assert run_logout(settings) == ExitCode.OK
    return caplog.text


def test_the_deleted_file_is_named(tmp_path, caplog):
    """Where the token was is the one thing the user cannot look up."""
    assert "garmin_tokens.json" in report(cached(tmp_path), caplog)


def test_it_says_the_next_run_will_ask_for_a_password(tmp_path, caplog):
    assert "ask you to log in" in report(cached(tmp_path), caplog)


def test_it_admits_the_token_is_not_revoked(tmp_path, caplog):
    """The surprising half: the file is this machine's copy, not the token."""
    assert "does not revoke" in report(cached(tmp_path), caplog)


def test_nothing_cached_is_not_a_failure(tmp_path, caplog):
    """Being signed out already is the state the command exists to reach."""
    settings = GarminSettings(token_store=str(tmp_path / "absent"))

    said = report(settings, caplog)

    assert "nothing to do" in said
    assert "does not revoke" not in said, "nothing was deleted to explain"
