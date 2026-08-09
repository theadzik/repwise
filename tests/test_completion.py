"""The completion scripts, and whether the shells agree they are scripts.

Two kinds of test here. The first reads the generated text and asks whether
everything the parser accepts made it in, which is the whole point of
generating it. The second hands the text to bash and asks what it would
actually complete - slower, and worth it: a script that reads correctly and
loads cleanly can still complete nothing at all.

zsh is checked for syntax and for registering itself, but not driven: doing
that needs a pty, and one is not always there to be had.
"""

import argparse
import shutil
import subprocess

import pytest

from repwise.cli import build_parser
from repwise.cli.completion import RENDERERS, render
from repwise.cli.parser import CATALOG, SHELLS

SHELL_MISSING = "the shell under test is not installed"


@pytest.fixture(params=sorted(SHELLS))
def script(request) -> str:
    """The generated script, once per shell, so tests state one thing twice."""
    return render(build_parser(), request.param)


def subcommands() -> dict[str, argparse.ArgumentParser]:
    """Every subparser, keyed by the word that reaches it."""
    for action in build_parser()._actions:  # noqa: SLF001
        if isinstance(action.choices, dict):
            return action.choices
    raise AssertionError("the parser has no subcommands")


def spec(script: str, flag: str) -> str:
    """The one `_arguments` line a zsh script devotes to this flag."""
    for line in script.splitlines():
        if line.strip().startswith(f"'{flag}["):
            return line.strip().removesuffix("\\").strip()
    raise AssertionError(f"no spec for {flag}")


# --- what the scripts say -------------------------------------------------


def test_a_renderer_exists_for_every_shell_the_parser_accepts():
    """`completion fish` should be a parse error, not a KeyError."""
    assert set(RENDERERS) == set(SHELLS)


def test_every_command_is_offered(script):
    for name in subcommands():
        assert name in script, f"{name} completes nowhere"


def test_every_option_of_every_command_is_offered(script):
    """The drift this module exists to prevent, stated as a test."""
    for sub in subcommands().values():
        for action in sub._actions:  # noqa: SLF001
            for flag in action.option_strings:
                assert flag in script, f"{flag} completes nowhere"


def test_the_catalog_keyword_is_offered(script):
    """`fetch` has no `choices` to enumerate, so this is easy to lose."""
    assert CATALOG in script


def test_zsh_completes_files_after_an_option_that_names_one():
    assert spec(render(build_parser(), "zsh"), "--config").endswith(":path:_files'")


def test_zsh_offers_nothing_for_a_value_it_cannot_guess():
    """An id takes a value, but Garmin is the only place to look one up, and
    a press of Tab should not log you in to find out."""
    activity = spec(render(build_parser(), "zsh"), "--activity")

    assert activity.endswith(":id:'"), "expected an empty action, not _files"


# --- what the shells make of them -----------------------------------------


@pytest.mark.parametrize("shell", sorted(SHELLS))
def test_the_script_parses_as_the_shell_it_is_for(shell, tmp_path):
    path = tmp_path / "completion"
    path.write_text(render(build_parser(), shell))
    if shutil.which(shell) is None:
        pytest.skip(SHELL_MISSING)

    check = subprocess.run(
        [shell, "-n", str(path)], capture_output=True, text=True, check=False
    )

    assert check.returncode == 0, check.stderr


def test_zsh_registers_itself(tmp_path):
    """Sourcing it should leave `repwise` completed by our function."""
    if shutil.which("zsh") is None:
        pytest.skip(SHELL_MISSING)
    path = tmp_path / "_repwise"
    path.write_text(render(build_parser(), "zsh"))

    loaded = subprocess.run(
        [
            "zsh",
            "-f",
            "-c",
            f"autoload -Uz compinit && compinit -u -D; source {path}; "
            "print ${_comps[repwise]:-NONE}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert loaded.stdout.strip() == "_repwise", loaded.stderr


def complete(argv: list[str], tmp_path) -> list[str]:
    """What bash would offer, given a command line with the cursor at its end.

    The completion function reads the words and the cursor position out of the
    environment and answers in COMPREPLY, so a shell that sets those up by
    hand can be asked the same question a Tab press asks.
    """
    path = tmp_path / "completion.bash"
    path.write_text(render(build_parser(), "bash"))
    words = " ".join(f'"{word}"' for word in argv)
    asked = subprocess.run(
        [
            "bash",
            "-c",
            f"source {path}\n"
            f"COMP_WORDS=({words})\n"
            f"COMP_CWORD={len(argv) - 1}\n"
            "COMPREPLY=()\n"
            "_repwise\n"
            'printf "%s\\n" "${COMPREPLY[@]}"\n',
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return [line for line in asked.stdout.splitlines() if line]


@pytest.fixture
def bash(tmp_path):
    if shutil.which("bash") is None:
        pytest.skip(SHELL_MISSING)

    def ask(*argv: str) -> list[str]:
        return complete(list(argv), tmp_path)

    return ask


def test_bash_offers_the_commands_first(bash):
    assert bash("repwise", "") == sorted(subcommands())


def test_bash_narrows_to_the_prefix_typed(bash):
    assert bash("repwise", "up") == ["update"]


def test_bash_offers_a_commands_own_options(bash):
    assert bash("repwise", "update", "--a") == ["--activity", "--apply"]


def test_bash_offers_the_catalog_keyword_to_fetch(bash):
    assert bash("repwise", "fetch", "ex") == [CATALOG]


def test_bash_offers_the_shells_to_completion(bash):
    assert bash("repwise", "completion", "") == sorted(SHELLS)


def test_bash_offers_global_options_before_a_command(bash):
    assert "--version" in bash("repwise", "-")


def test_bash_does_not_offer_global_options_after_a_command(bash):
    """--version is the top level's; argparse rejects it after a command."""
    assert "--version" not in bash("repwise", "update", "-")


def test_bash_looks_past_an_option_and_its_value_for_the_command(bash):
    """`--config x.yaml` is two words, and neither of them is the command."""
    assert bash("repwise", "--config", "x.yaml", "ch") == ["check"]


def test_bash_completes_files_after_an_option_that_names_one(bash, tmp_path):
    (tmp_path / "routine.yaml").write_text("")
    offered = bash("repwise", "--config", str(tmp_path / "rout"))

    assert offered == [str(tmp_path / "routine.yaml")]


def test_bash_completes_nothing_after_an_option_it_cannot_guess(bash, tmp_path):
    """An activity id is a number in Garmin, so filenames would be noise."""
    (tmp_path / "routine.yaml").write_text("")

    assert bash("repwise", "update", "--activity", str(tmp_path / "rout")) == []
