"""Argument parsing and help output."""

import pytest

from workout.cli import build_parser


def help_text(argv: list[str], capsys) -> str:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(argv)
    return capsys.readouterr().out


def test_examples_keep_their_line_breaks(capsys):
    """argparse reflows help text unless told not to, squashing the examples."""
    out = help_text(["--help"], capsys)
    assert "\n  workout update --apply    write those targets back to Garmin\n" in out


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
    assert args.dump is False
    assert args.activity is None


def test_update_accepts_its_flags():
    args = build_parser().parse_args(["update", "--apply", "--activity", "42", "--dump"])
    assert args.apply and args.dump
    assert args.activity == "42"


def test_fetch_takes_any_number_of_ids():
    assert build_parser().parse_args(["fetch"]).workout_ids == []
    assert build_parser().parse_args(["fetch", "1", "2"]).workout_ids == ["1", "2"]


def test_a_command_is_required(capsys):
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])
    assert "required" in capsys.readouterr().err


def test_config_is_a_global_option():
    args = build_parser().parse_args(["--config", "other.yaml", "update"])
    assert args.config == "other.yaml"
