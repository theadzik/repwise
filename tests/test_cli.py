"""Argument parsing and help output."""

import pytest

from repwise.cli import build_parser


def help_text(argv: list[str], capsys) -> str:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(argv)
    return capsys.readouterr().out


def test_examples_keep_their_line_breaks(capsys):
    """argparse reflows help text unless told not to, squashing the examples."""
    out = help_text(["--help"], capsys)
    assert "\n  repwise update --apply    write it back to Garmin\n" in out


def test_top_level_help_lists_both_commands(capsys):
    out = help_text(["--help"], capsys)
    assert "update" in out and "fetch" in out


def test_subcommand_help_is_wrapped(capsys):
    """Subcommand prose has no fixed layout, so it should wrap to the width."""
    out = help_text(["update", "--help"], capsys)
    body = [line for line in out.splitlines() if line and not line.startswith(" ")]
    assert body, "expected some description text"
    assert max(len(line) for line in body) < 100


def test_update_defaults_to_a_dry_run():
    args = build_parser().parse_args(["update"])
    assert args.apply is False
    assert args.activity is None


def test_update_accepts_its_flags():
    args = build_parser().parse_args(["update", "--apply", "--activity", "42"])
    assert args.apply
    assert args.activity == "42"


def test_fetch_takes_a_target_and_any_number_of_ids():
    args = build_parser().parse_args(["fetch", "workouts", "1", "2"])
    assert args.target == "workouts"
    assert args.ids == ["1", "2"]


def test_fetch_takes_a_target_on_its_own():
    for target in ("workouts", "activities", "exercises"):
        args = build_parser().parse_args(["fetch", target])
        assert args.target == target
        assert args.ids == []


def test_fetch_needs_a_target(capsys):
    """The first word says what the ids after it are, so there has to be one."""
    with pytest.raises(SystemExit):
        build_parser().parse_args(["fetch"])
    assert "required" in capsys.readouterr().err


def test_fetch_targets_are_argparse_choices(capsys):
    """Which words mean something is declared, so argparse rejects the rest.

    It could not be while a bare id meant `workouts`: `choices` would have had
    to accept every number there is.
    """
    with pytest.raises(SystemExit):
        build_parser().parse_args(["fetch", "nonsense"])
    assert "invalid choice" in capsys.readouterr().err


def test_a_command_is_required(capsys):
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])
    assert "required" in capsys.readouterr().err


def test_config_is_a_global_option():
    args = build_parser().parse_args(["--config", "other.yaml", "update"])
    assert args.config == "other.yaml"


def test_config_defaults_to_being_searched_for():
    """No path is frozen into the parser; config.py decides where to look."""
    assert build_parser().parse_args(["update"]).config is None


def test_version_reports_the_packaged_version(capsys):
    """The number commitizen bumps, so a bug report can carry it."""
    from repwise import __version__

    out = help_text(["--version"], capsys)

    assert out.strip() == f"repwise {__version__}"


# --- the Garmin-loading commands ------------------------------------------


def test_list_defaults_to_strength_only():
    args = build_parser().parse_args(["list"])
    assert args.all is False


def test_import_defaults_to_stdout():
    args = build_parser().parse_args(["import"])
    assert args.output is None, "no -o means print"
    assert args.force is False
    assert args.name is None and args.id is None


def test_import_output_takes_a_path():
    args = build_parser().parse_args(["import", "-o", "out.yaml"])
    assert args.output == "out.yaml"


def test_import_name_and_id_are_mutually_exclusive(capsys):
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["import", "--name", "A", "--id", "1"])
    assert "not allowed with" in capsys.readouterr().err


def test_check_takes_no_arguments():
    args = build_parser().parse_args(["check"])
    assert args.command == "check"


def test_logout_takes_no_arguments():
    args = build_parser().parse_args(["logout"])
    assert args.command == "logout"


def test_push_defaults_off():
    assert build_parser().parse_args(["update"]).push is False


def test_push_is_accepted_with_apply():
    args = build_parser().parse_args(["update", "--apply", "--push"])
    assert args.apply and args.push


def test_verbose_is_off_by_default():
    assert build_parser().parse_args(["update"]).verbose is False


@pytest.mark.parametrize(
    "argv",
    [
        ["-v", "update"],
        ["--verbose", "update"],
        ["update", "-v"],
        ["update", "--verbose"],
    ],
    ids=["short-before", "long-before", "short-after", "long-after"],
)
def test_verbose_is_accepted_on_either_side_of_the_command(argv):
    """A subcommand default must not overwrite a flag given before it."""
    assert build_parser().parse_args(argv).verbose is True
