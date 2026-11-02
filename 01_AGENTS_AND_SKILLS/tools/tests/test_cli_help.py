"""Help and argument-handling contract for the CLI.

An agent discovers what it is allowed to run by reading ``--help``, so help output that is
missing, broken or out of step with the subcommand list is a real defect, not cosmetics.
"""

from __future__ import annotations

import pytest

import learningos
from common import EXIT_OK, EXIT_USAGE

COMMAND_NAMES = [command.name for command in learningos.COMMANDS]


def test_top_level_help_exits_zero_and_lists_every_subcommand(cli):
    result = cli("--help")
    assert result.code == EXIT_OK
    for name in COMMAND_NAMES:
        assert name in result.out, f"{name} is missing from the top-level help"
    assert "exit codes" in result.out


@pytest.mark.parametrize("name", COMMAND_NAMES)
def test_subcommand_help_exits_zero_and_documents_itself(cli, name):
    result = cli(name, "--help")
    assert result.code == EXIT_OK, result.text
    assert f"python learningos.py {name}" in result.out, "the epilog must show a real example"
    assert "--workspace" in result.out, "global options must be accepted after the subcommand too"


def test_version_exits_zero(cli):
    result = cli("--version")
    assert result.code == EXIT_OK
    assert "learningos" in result.out


def test_no_command_is_a_usage_error(cli):
    assert cli().code == EXIT_USAGE


def test_unknown_command_is_a_usage_error(cli):
    assert cli("teach-me-everything").code == EXIT_USAGE


def test_unknown_option_is_a_usage_error(cli):
    assert cli("validate", "--not-a-flag").code == EXIT_USAGE


def test_missing_required_option_is_a_usage_error(cli):
    assert cli("init-subject", "--id", "algo").code == EXIT_USAGE


def test_workspace_option_is_accepted_before_the_subcommand(ws):
    assert learningos.main(["--workspace", str(ws), "--quiet", "validate"]) == EXIT_OK


def test_unknown_workspace_is_a_usage_error(tmp_path):
    assert learningos.main(["validate", "--workspace", str(tmp_path / "nowhere")]) == EXIT_USAGE


def test_directory_without_marker_is_a_usage_error(tmp_path):
    (tmp_path / "empty").mkdir()
    assert learningos.main(["validate", "--workspace", str(tmp_path / "empty")]) == EXIT_USAGE
