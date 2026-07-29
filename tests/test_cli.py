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
    args = build_parser().parse_args(
        ["update", "--apply", "--activity", "42", "--dump"]
    )
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
    assert args.func.__name__ == "command_check"


def test_push_defaults_off():
    assert build_parser().parse_args(["update"]).push is False


def test_push_is_accepted_with_apply():
    args = build_parser().parse_args(["update", "--apply", "--push"])
    assert args.apply and args.push


def test_push_without_apply_is_refused(caplog):
    """Nothing has been written yet, so there is nothing to send."""
    import logging

    from workout.cli import EXIT_CONFIG, command_update
    from workout.domain.models import Config

    args = build_parser().parse_args(["update", "--push"])
    with caplog.at_level(logging.ERROR):
        code = command_update(args, Config({}))
    assert code == EXIT_CONFIG
    assert "only makes sense with --apply" in caplog.text


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
